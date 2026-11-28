//! `neosian harness-watch` (hidden; §40.50 item 3, amended by #430 and
//! #431): one turn's watcher. It runs only where the tool is enabled, not
//! in a child and with a home directory, and reads a plan, never a command:
//! one line of JSON, then exactly that many bytes of prompt on its standard
//! input, which the server then holds open. It checks the plan with the
//! tool's own rules, builds the harness's argument vector through the
//! adapter, on macOS under neosian's common filesystem policy (`sandbox/`,
//! X4b), hands it the prompt on a pipe and relays its output, which also
//! reaches the turn's record (X4a). When the harness exits, the watcher
//! files the turn through the record verb (`filing.rs`), reports its status
//! and ends what the harness left in its group after a grace; when its
//! standard input ends first (the server stopped the turn, or the server is
//! gone by any cause), it ends the harness and every descendant it can find,
//! and files nothing.

use std::io::{self, BufRead, Read, Write};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{ExitStatus, Stdio};
use std::sync::{Arc, Mutex, PoisonError, mpsc};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use clap::Args;

use super::adapter::{self, Agent, McpServer, Mode, Request};
use super::capture::Capture;
use super::ending::{self, ignore_sigterm};
use super::filing;
use super::plan::{Checked, PLAN_BYTES, WirePlan, check, read_only};
use super::probe;
use super::sandbox::{self, Launch};
use super::text::say;

/// How long a turn's processes have to end after SIGTERM.
pub const GRACE: Duration = Duration::from_secs(5);
/// The line the watcher ends its stderr with: the nonce and the status.
pub const EXIT_LINE: &str = "neosian-harness-exit";

/// One turn's watcher (hidden; started only by the MCP server).
#[derive(Args)]
pub struct Watch {}

fn refuse(err: &mut impl Write, sentence: &str) -> io::Result<u8> {
    writeln!(err, "{sentence}")?;
    Ok(2)
}

fn code(status: ExitStatus) -> u8 {
    match (status.code(), status.signal()) {
        (Some(code), _) => u8::try_from(code).unwrap_or(1),
        (None, Some(signal)) => u8::try_from(128 + signal).unwrap_or(1),
        _ => 1,
    }
}

/// The plan line and the prompt that follows it.
fn read_plan(input: &mut impl BufRead) -> Result<(WirePlan, Vec<u8>), String> {
    let mut line = Vec::new();
    input
        .take(PLAN_BYTES as u64)
        .read_until(b'\n', &mut line)
        .map_err(|e| e.to_string())?;
    let plan: WirePlan =
        serde_json::from_slice(&line).map_err(|_| say("watch_bad_field", &[("field", "plan")]))?;
    let wanted = plan.prompt_bytes.min(adapter::PROMPT_BYTES);
    let mut prompt = vec![0; wanted];
    input
        .read_exact(&mut prompt)
        .map_err(|_| say("watch_bad_field", &[("field", "prompt_bytes")]))?;
    Ok((plan, prompt))
}

/// The turn's private directory: mode 0700 with the child marker, Codex's
/// TMPDIR. Removed when the watcher ends; a stale one goes at the next start.
fn private_dir() -> io::Result<PathBuf> {
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

/// This neosian's own server for a read-only child (#431): the running
/// executable, the server's store selection, and every mount read-only.
fn own_server(plan: &Checked) -> io::Result<McpServer> {
    let exe = std::env::current_exe()?;
    let exe = exe.canonicalize().unwrap_or(exe);
    let mut args = vec!["mcp".to_string()];
    args.extend(plan.selection.iter().cloned());
    for mount in &plan.mounts {
        args.extend(["--mount".to_string(), read_only(mount)]);
    }
    args.extend(["--actor".to_string(), format!("mcp:{}", plan.agent.name())]);
    Ok(McpServer {
        command: exe.display().to_string(),
        args,
        mounted: !plan.mounts.is_empty(),
    })
}

/// The turn's record while it runs; the filing takes it out at the end.
type Recording = Arc<Mutex<Option<Capture>>>;

/// Copy the harness's stream to the watcher's own descriptor `fd` (1 or
/// 2) as it arrives, written through the descriptor number itself: the
/// command door holds the standard streams' locks for the watcher's whole
/// run, and a second descriptor would keep the server's pipe open after the
/// watcher points 1 and 2 at /dev/null. Standard output also reaches the
/// turn's record, whole, after it is relayed (X4a).
fn relay(
    mut from: impl Read + Send + 'static,
    fd: i32,
    record: Option<Recording>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut to = ending::raw(fd);
        let mut chunk = [0u8; 8192];
        while let Ok(n) = from.read(&mut chunk) {
            if n == 0 || to.write_all(&chunk[..n]).is_err() {
                break;
            }
            if let Some(record) = &record
                && let Some(capture) = record
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .as_mut()
            {
                capture.push(&chunk[..n]);
            }
        }
    })
}

pub fn run(_args: &Watch, err: &mut impl Write) -> io::Result<u8> {
    let mut warned = Vec::new();
    let Some(settings) = super::served(&mut |line: &str| warned.push(line.to_string())) else {
        return refuse(err, &say("watch_off", &[]));
    };
    let read = read_plan(&mut io::stdin().lock());
    let (wire, prompt) = match read {
        Ok(read) => read,
        Err(sentence) => return refuse(err, &sentence),
    };
    let home = adapter::home();
    let plan = match check(wire, home.as_deref(), &settings.directories) {
        Ok(plan) => plan,
        Err(sentence) => return refuse(err, &sentence),
    };
    let path = std::env::var_os("PATH");
    let Some(program) = adapter::which(plan.agent.program(), path.as_deref()) else {
        let sentence = say("watch_missing", &[("agent", plan.agent.name())]);
        return refuse(err, &sentence);
    };
    let (codex_keys, codex_off) = if plan.agent == Agent::Codex {
        let keys = match probe::codex_keys(&program, &plan.directory) {
            Ok(keys) => keys,
            Err((key, error)) => {
                let sentence = say("codex_probe_failed", &[("key", &key), ("error", &error)]);
                return refuse(err, &sentence);
            }
        };
        let off = match plan.mode {
            Mode::Write => Vec::new(),
            Mode::ReadOnly => match probe::codex_others(&program, &plan.directory) {
                Ok(off) => off,
                Err(others) => return refuse(err, &others.sentence()),
            },
        };
        (keys, off)
    } else {
        (Vec::new(), Vec::new())
    };
    let private = private_dir()?;
    let server = own_server(&plan)?;
    // On macOS the common policy is built and proved on a probe write before
    // anything starts; a failure refuses the turn (X4b).
    let turn = sandbox::Turn {
        agent: plan.agent,
        mode: plan.mode,
        directory: &plan.directory,
        private: &private,
        selection: &plan.selection,
    };
    let launch = match sandbox::prepare(&turn) {
        Ok(launch) => launch,
        Err(sentence) => {
            let _ = std::fs::remove_dir_all(&private);
            return refuse(err, &sentence);
        }
    };
    let opencode_config = std::env::var(super::opencode::CONFIG_ENV).ok();
    let request = Request {
        agent: plan.agent,
        mode: plan.mode,
        model: plan.model.as_deref(),
        directory: &plan.directory,
        resume: plan.resume.as_deref(),
        new_session: &plan.new_session,
        private: &private,
        codex_keys: &codex_keys,
        opencode_config: opencode_config.as_deref(),
        parent: plan.parent.as_deref(),
        server: Some(&server),
        codex_off: &codex_off,
        confined: launch.confined(),
    };
    let status = match adapter::plan(&request) {
        Ok(built) => drive(&plan, &launch, &program, &built.argv, &built.env, prompt),
        Err(_) => refuse(err, &say("bad_opencode_config", &[])),
    };
    let _ = std::fs::remove_dir_all(&private);
    status
}

fn drive(
    plan: &Checked,
    launch: &Launch,
    program: &Path,
    argv: &[String],
    env: &[(String, String)],
    prompt: Vec<u8>,
) -> io::Result<u8> {
    let mut harness = launch
        .command(program, &argv[1..], env)
        .current_dir(&plan.directory)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    // Only now, so the harness keeps the default disposition: the group's
    // SIGTERM is for everyone but the watcher, which sends it.
    ignore_sigterm();
    let asked = prompt.clone();
    let mut stdin = harness.stdin.take().expect("piped");
    thread::spawn(move || {
        let _ = stdin.write_all(&prompt);
    });
    let record: Recording = Arc::new(Mutex::new(Some(Capture::new(plan.agent))));
    let relays = [
        relay(
            harness.stdout.take().expect("piped"),
            1,
            Some(Arc::clone(&record)),
        ),
        relay(harness.stderr.take().expect("piped"), 2, None),
    ];
    let (ended, closed) = mpsc::channel::<()>();
    thread::spawn(move || {
        let mut sink = [0u8; 256];
        let mut input = io::stdin();
        while matches!(input.read(&mut sink), Ok(n) if n > 0) {}
        let _ = ended.send(());
    });
    let own = std::process::id() as i32;
    let status = loop {
        if let Some(status) = harness.try_wait()? {
            break Some(status);
        }
        match closed.recv_timeout(Duration::from_millis(20)) {
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break None,
        }
    };
    let Some(status) = status else {
        ending::end_tree(&mut harness, own);
        return Ok(1);
    };
    let drained = Instant::now() + Duration::from_millis(500);
    for handle in relays {
        while !handle.is_finished() && Instant::now() < drained {
            thread::sleep(Duration::from_millis(10));
        }
    }
    let code = code(status);
    // The turn is filed before the answer, so the parent reads it at once,
    // or is told it waits for its destination (X4a).
    let captured = record
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .take()
        .map(Capture::finish);
    let filed = filing::file(plan, &asked, captured, code);
    filing::report(&plan.nonce, &filed);
    // The result first: the exit line, then the server's pipes closed, so
    // the answer does not wait for what the harness left behind (#431).
    ending::report(&plan.nonce, code);
    ending::leftovers(own, &closed);
    Ok(code)
}
