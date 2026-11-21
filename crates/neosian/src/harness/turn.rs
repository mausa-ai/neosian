//! One turn of a child (§40.50 items 3, 4, 8): the watcher process and the
//! threads that follow it. The supervisor thread owns the process; the
//! readers turn its output into events; the registry reads the shared
//! state and asks for a stop. Every way a turn ends goes through the
//! watcher's pipe, with SIGKILL on the group as the fallback.

use std::io::{self, BufRead, BufReader, Read};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::adapter::Agent;
use super::events::{Event, Tail, Transcript};
use super::watch::{GRACE, SIGKILL, signal_group};

/// The longest line of harness output read; the rest of a longer one is skipped.
const LINE_BYTES: usize = 8 * 1024 * 1024;
/// After the watcher exits, how long the readers may take to reach the end.
const DRAIN: Duration = Duration::from_secs(1);
/// After the pipe closes, how long the server waits before SIGKILL itself.
const FALLBACK: Duration = Duration::from_secs(3);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum End {
    Exited(i32),
    Stopped,
    TimedOut,
}

/// What the threads learned, read by the registry.
#[derive(Default)]
pub struct Shared {
    pub transcript: Transcript,
    pub last_text: Option<String>,
    pub answer: Option<String>,
    pub failure: Option<String>,
    pub stderr: Tail,
    pub session: Option<String>,
    pub end: Option<End>,
    ended_at: Option<Instant>,
    eof: bool,
}

impl Shared {
    fn apply(&mut self, event: Event) {
        match event {
            Event::Session(session) => {
                if self.session.is_none() && super::adapter::session_ok(&session) {
                    self.session = Some(session);
                }
            }
            Event::Text(text) => {
                self.transcript.push(&format!("{text}\n"));
                self.last_text = Some(text);
            }
            Event::Answer(answer) => self.answer = Some(answer),
            Event::Note(line) => self.transcript.push(&format!("{line}\n")),
            Event::Failure(failure) => {
                self.transcript.push(&format!("[failed] {failure}\n"));
                self.failure = Some(failure);
            }
        }
    }

    /// The turn is over and its output read (or the drain time has passed).
    pub fn ended(&self) -> bool {
        self.end.is_some() && (self.eof || self.ended_at.is_some_and(|at| at.elapsed() >= DRAIN))
    }

    pub fn final_answer(&self) -> Option<&str> {
        self.answer.as_deref().or(self.last_text.as_deref())
    }
}

pub fn lock(shared: &Mutex<Shared>) -> MutexGuard<'_, Shared> {
    shared.lock().unwrap_or_else(PoisonError::into_inner)
}

pub struct Turn {
    pub number: u32,
    pub shared: Arc<Mutex<Shared>>,
    /// The newest turn number of the child's conversation when it began,
    /// or why it could not be read.
    pub before: Result<u64, String>,
    /// How much of the transcript the parent has read.
    pub read: usize,
    stop: Arc<AtomicBool>,
    supervisor: Option<JoinHandle<()>>,
}

/// Read one line of at most `LINE_BYTES`; false at the end of the stream.
fn bounded_line(reader: &mut impl BufRead, line: &mut Vec<u8>) -> io::Result<bool> {
    line.clear();
    let mut seen = false;
    loop {
        let buffer = reader.fill_buf()?;
        if buffer.is_empty() {
            return Ok(seen);
        }
        seen = true;
        let (chunk, done) = match buffer.iter().position(|b| *b == b'\n') {
            Some(at) => (&buffer[..at], at + 1),
            None => (buffer, buffer.len()),
        };
        if line.len() < LINE_BYTES {
            let room = LINE_BYTES - line.len();
            line.extend_from_slice(&chunk[..chunk.len().min(room)]);
        }
        let newline = done > chunk.len();
        reader.consume(done);
        if newline {
            return Ok(true);
        }
    }
}

impl Turn {
    /// Start the watcher for `argv` in `dir` with the prompt file, and the
    /// threads that follow it.
    pub fn begin(
        agent: Agent,
        number: u32,
        argv: &[String],
        env: &[(String, String)],
        dir: &Path,
        prompt: &Path,
        limit: Duration,
    ) -> io::Result<Self> {
        let mut child: Child = Command::new(std::env::current_exe()?)
            .arg("harness-watch")
            .arg(prompt)
            .arg("--")
            .args(argv)
            .envs(env.iter().map(|(key, value)| (key, value)))
            .current_dir(dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()?;
        let shared = Arc::new(Mutex::new(Shared::default()));
        let stdout = child.stdout.take().expect("piped");
        let stderr = child.stderr.take().expect("piped");
        let out = Arc::clone(&shared);
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            let mut line = Vec::new();
            while let Ok(true) = bounded_line(&mut reader, &mut line) {
                let text = String::from_utf8_lossy(&line);
                let events = agent.read(&text);
                let mut shared = lock(&out);
                events.into_iter().for_each(|event| shared.apply(event));
            }
            lock(&out).eof = true;
        });
        let err = Arc::clone(&shared);
        thread::spawn(move || {
            let mut stderr = stderr;
            let mut chunk = [0u8; 4096];
            while let Ok(n) = stderr.read(&mut chunk) {
                if n == 0 {
                    break;
                }
                lock(&err)
                    .stderr
                    .push(&String::from_utf8_lossy(&chunk[..n]));
            }
        });
        let stop = Arc::new(AtomicBool::new(false));
        let asked = Arc::clone(&stop);
        let state = Arc::clone(&shared);
        let supervisor = thread::spawn(move || supervise(child, &asked, &state, limit));
        Ok(Self {
            number,
            shared,
            before: Ok(0),
            read: 0,
            stop,
            supervisor: Some(supervisor),
        })
    }

    pub fn ended(&self) -> bool {
        lock(&self.shared).ended()
    }

    /// Ask the supervisor to end the turn; it returns at once.
    pub fn ask_stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    /// Wait for the supervisor, at most `bound`; true once it has finished.
    pub fn join(&mut self, bound: Duration) -> bool {
        let deadline = Instant::now() + bound;
        while self
            .supervisor
            .as_ref()
            .is_some_and(|handle| !handle.is_finished())
        {
            if Instant::now() >= deadline {
                return false;
            }
            thread::sleep(Duration::from_millis(10));
        }
        if let Some(handle) = self.supervisor.take() {
            let _ = handle.join();
        }
        true
    }

    /// How long a stop may take: the watcher's grace, the fallback, a margin.
    pub fn stop_bound() -> Duration {
        GRACE + FALLBACK + Duration::from_secs(1)
    }
}

fn supervise(mut child: Child, asked: &AtomicBool, shared: &Mutex<Shared>, limit: Duration) {
    let deadline = Instant::now() + limit;
    let end = loop {
        match child.try_wait() {
            Ok(Some(status)) => break End::Exited(status.code().unwrap_or(1)),
            Ok(None) => {}
            Err(_) => break End::Exited(1),
        }
        let timed_out = Instant::now() >= deadline;
        if timed_out || asked.load(Ordering::SeqCst) {
            terminate(&mut child);
            break if timed_out {
                End::TimedOut
            } else {
                End::Stopped
            };
        }
        thread::sleep(Duration::from_millis(25));
    };
    let mut shared = lock(shared);
    shared.end = Some(end);
    shared.ended_at = Some(Instant::now());
}

/// Close the watcher's pipe so it ends its group; SIGKILL the group if the
/// watcher has not exited after its grace and the fallback.
fn terminate(child: &mut Child) {
    drop(child.stdin.take());
    let deadline = Instant::now() + GRACE + FALLBACK;
    while Instant::now() < deadline {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        thread::sleep(Duration::from_millis(20));
    }
    // Not reaped yet, so the group's ID cannot have been reused.
    signal_group(child.id(), SIGKILL);
    let _ = child.wait();
}

/// A child's private directory: mode 0700, holding the marker file.
pub fn private_dir() -> io::Result<PathBuf> {
    use std::os::unix::fs::DirBuilderExt;
    let dir = std::env::temp_dir().join(format!(
        "neosian-harness-{}-{}",
        std::process::id(),
        uuid::Uuid::new_v4().simple()
    ));
    std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
    std::fs::write(dir.join(super::MARKER), "")?;
    Ok(dir)
}

/// The prompt for one turn, readable by its owner alone.
pub fn prompt_file(dir: &Path, number: u32, prompt: &str) -> io::Result<PathBuf> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let path = dir.join(format!("prompt-{number}"));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)?;
    file.write_all(prompt.as_bytes())?;
    Ok(path)
}
