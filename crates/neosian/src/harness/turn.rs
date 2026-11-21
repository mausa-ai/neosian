//! One turn of a child (§40.50 items 3, 4, 8, amended by #430): the
//! watcher process and the threads that follow it. The supervisor thread
//! writes the plan and the prompt to the watcher, holds its standard input
//! open for the turn's life, and closes it to stop the turn. The watcher is
//! the only writer of the pipes the readers read, so they reach the end
//! when it exits, whatever else still holds the harness's own pipes. When
//! the watcher is gone, the server ends its process group itself.

use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::Value;

use super::adapter::{self, Agent};
use super::events::{Event, Log, Tail, Text};
use super::tree::{self, SIGKILL};
use super::watch::{EXIT_LINE, GRACE};

/// The longest line of harness output read; the rest of a longer one is skipped.
const LINE_BYTES: usize = 8 * 1024 * 1024;
/// After the watcher exits, how long the readers may take to reach the end.
const DRAIN: Duration = Duration::from_secs(1);
/// After the pipe closes, how long the server waits before ending the group itself.
const FALLBACK: Duration = Duration::from_secs(3);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum End {
    Exited(i32),
    Stopped,
    TimedOut,
    /// The watcher died by a signal it did not send itself.
    Lost,
}

/// Whether an announced session already existed in this store.
pub type Announce = Box<dyn Fn(&str) -> bool + Send>;

/// What the threads learned, read by the registry.
#[derive(Default)]
pub struct Shared {
    pub text: Text,
    pub log: Log,
    pub last_text: Option<String>,
    pub answer: Option<String>,
    pub failure: Option<String>,
    pub stderr: Tail,
    pub session: Option<String>,
    /// The announced session already existed when it was announced.
    pub existed: bool,
    pub end: Option<End>,
    ended_at: Option<Instant>,
    eof: bool,
}

impl Shared {
    fn apply(&mut self, event: Event, announce: &mut Option<Announce>) {
        match event {
            Event::Session(session) => {
                if self.session.is_none() && adapter::session_ok(&session) {
                    if let Some(check) = announce.take() {
                        self.existed = check(&session);
                    }
                    self.session = Some(session);
                }
            }
            Event::Text(text) => {
                if self.text.total() > 0 {
                    self.text.push("\n");
                }
                self.text.push(&text);
                self.last_text = Some(text);
            }
            Event::Answer(answer) => self.answer = Some(answer),
            Event::Observed(entry) => self.log.push(entry),
            Event::Failure(failure) => {
                self.log.push(serde_json::json!({"kind": "failure"}));
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
    pub shared: Arc<Mutex<Shared>>,
    /// The newest turn number of the child's conversation when it began,
    /// or why it could not be read.
    pub before: Result<u64, String>,
    /// How much of the text and how many entries the parent has read.
    pub read_text: usize,
    pub read_events: usize,
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
    /// Start the watcher in `dir` and hand it `plan` and the prompt.
    pub fn begin(
        agent: Agent,
        plan: &Value,
        prompt: &str,
        dir: &Path,
        limit: Duration,
        announce: Option<Announce>,
        nonce: String,
    ) -> io::Result<Self> {
        let mut child = Command::new(std::env::current_exe()?)
            .arg("harness-watch")
            .current_dir(dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()?;
        let mut message = plan.to_string().into_bytes();
        message.push(b'\n');
        message.extend_from_slice(prompt.as_bytes());
        let shared = Arc::new(Mutex::new(Shared::default()));
        let stdout = child.stdout.take().expect("piped");
        let stderr = child.stderr.take().expect("piped");
        let out = Arc::clone(&shared);
        thread::spawn(move || {
            let mut announce = announce;
            let mut reader = BufReader::new(stdout);
            let mut line = Vec::new();
            while let Ok(true) = bounded_line(&mut reader, &mut line) {
                let events = agent.read(&String::from_utf8_lossy(&line));
                let mut shared = lock(&out);
                for event in events {
                    shared.apply(event, &mut announce);
                }
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
            exit_line(&mut lock(&err), &nonce);
        });
        let stop = Arc::new(AtomicBool::new(false));
        let asked = Arc::clone(&stop);
        let state = Arc::clone(&shared);
        let supervisor = thread::spawn(move || supervise(child, message, &asked, &state, limit));
        Ok(Self {
            shared,
            before: Ok(0),
            read_text: 0,
            read_events: 0,
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
        GRACE + FALLBACK + GRACE + Duration::from_secs(1)
    }
}

fn supervise(
    mut child: Child,
    message: Vec<u8>,
    asked: &AtomicBool,
    shared: &Mutex<Shared>,
    limit: Duration,
) {
    let mut control = child.stdin.take();
    if let Some(stdin) = control.as_mut() {
        // A watcher that refused has exited; its stderr says why.
        let _ = stdin.write_all(&message).and_then(|()| stdin.flush());
    }
    let group = child.id() as i32;
    let deadline = Instant::now() + limit;
    let end = loop {
        match child.try_wait() {
            Ok(Some(status)) if status.signal().is_some() => break End::Lost,
            Ok(Some(status)) => break End::Exited(status.code().unwrap_or(1)),
            Ok(None) => {}
            Err(_) => break End::Lost,
        }
        let timed_out = Instant::now() >= deadline;
        if timed_out || asked.load(Ordering::SeqCst) {
            drop(control.take());
            terminate(&mut child, group);
            break if timed_out {
                End::TimedOut
            } else {
                End::Stopped
            };
        }
        thread::sleep(Duration::from_millis(25));
    };
    drop(control);
    // A watcher gone by a signal left its group behind; a finished one
    // left nothing, and this finds nothing.
    tree::end_group(group, GRACE);
    let mut shared = lock(shared);
    if shared.end.is_none() {
        shared.end = Some(end);
        shared.ended_at = Some(Instant::now());
    }
}

/// The watcher's own last line on stderr, `EXIT_LINE <nonce> <status>`,
/// which ends the turn before the watcher has finished cleaning up (#431).
/// Only the turn's nonce counts, so a harness cannot write it.
fn exit_line(shared: &mut Shared, nonce: &str) {
    let text = shared.stderr.text().to_string();
    let marker = format!("{EXIT_LINE} {nonce} ");
    let Some(at) = text.rfind(&marker) else {
        return;
    };
    let Ok(code) = text[at + marker.len()..].trim().parse::<i32>() else {
        return;
    };
    shared.stderr.replace(text[..at].trim_end());
    if shared.end.is_none() {
        shared.end = Some(End::Exited(code));
        shared.ended_at = Some(Instant::now());
    }
}

/// After the pipe closed: wait for the watcher to end the turn; if it has
/// not after its grace and the fallback, end its group from here.
fn terminate(child: &mut Child, group: i32) {
    let deadline = Instant::now() + GRACE + FALLBACK;
    while Instant::now() < deadline {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        thread::sleep(Duration::from_millis(20));
    }
    // Not reaped yet, so the group's ID cannot have been reused.
    tree::end_group(group, Duration::from_secs(1));
    tree::signal(-group, SIGKILL);
    let _ = child.wait();
}
