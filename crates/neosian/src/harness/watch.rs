//! `neosian harness-watch` (hidden; §40.50 item 3, amended by #430): one
//! turn's watcher. It runs only where the tool is enabled and not in a
//! child, and reads a plan, never a command: one line of JSON, then exactly
//! that many bytes of prompt on its standard input, which the server then
//! holds open. It builds the harness's argument vector through the
//! adapter, hands it the prompt on a pipe, relays its output, and exits
//! with its status. When its standard input ends first (the server stopped
//! the turn, or the server is gone by any cause), it ends the harness and
//! every descendant it can find.

use std::collections::BTreeSet;
use std::io::{self, BufRead, Read, Write};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::mpsc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use clap::Args;

use super::adapter::{self, McpServer, Request};
use super::plan::{Checked, PLAN_BYTES, WirePlan, check};
use super::text::say;
use super::tree::{self, SIGKILL, SIGTERM};

const SIG_IGN: usize = 1;
/// How long a turn's processes have to end after SIGTERM.
pub const GRACE: Duration = Duration::from_secs(5);
/// After a normal exit, how long late work in the group (a record hook
/// landing the turn) may take before it is ended.
const LATE: Duration = Duration::from_secs(3);

unsafe extern "C" {
    fn signal(signum: i32, handler: usize) -> usize;
}

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

/// This neosian's own server for a Claude Code read-only child.
fn own_server(store: &[String]) -> io::Result<McpServer> {
    let exe = std::env::current_exe()?;
    let exe = exe.canonicalize().unwrap_or(exe);
    let mut args = vec!["mcp".to_string()];
    args.extend(store.iter().cloned());
    args.extend(["--actor".to_string(), "mcp:claude-code".to_string()]);
    Ok(McpServer {
        command: exe.display().to_string(),
        args,
    })
}

/// Copy the harness's stream to the watcher's own descriptor `fd` as it
/// arrives. The descriptor is opened anew: the command door holds the
/// standard streams' locks for the watcher's whole run.
fn relay(mut from: impl Read + Send + 'static, fd: u8) -> JoinHandle<()> {
    thread::spawn(move || {
        let Ok(mut to) = std::fs::OpenOptions::new()
            .write(true)
            .open(format!("/dev/fd/{fd}"))
        else {
            return;
        };
        let mut chunk = [0u8; 8192];
        while let Ok(n) = from.read(&mut chunk) {
            if n == 0 || to.write_all(&chunk[..n]).is_err() {
                break;
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
    let home = std::env::home_dir();
    let plan = match check(wire, home.as_deref(), &settings.directories) {
        Ok(plan) => plan,
        Err(sentence) => return refuse(err, &sentence),
    };
    let path = std::env::var_os("PATH");
    let Some(program) = adapter::which(plan.agent.program(), path.as_deref()) else {
        let sentence = say("watch_missing", &[("agent", plan.agent.name())]);
        return refuse(err, &sentence);
    };
    let private = private_dir()?;
    let status = drive(&plan, &program, &private, prompt, io::stdin(), err);
    let _ = std::fs::remove_dir_all(&private);
    status
}

fn drive(
    plan: &Checked,
    program: &Path,
    private: &Path,
    prompt: Vec<u8>,
    mut input: impl Read + Send + 'static,
    err: &mut impl Write,
) -> io::Result<u8> {
    let server = own_server(&plan.store)?;
    let opencode_config = std::env::var(super::opencode::CONFIG_ENV).ok();
    let request = Request {
        agent: plan.agent,
        mode: plan.mode,
        model: plan.model.as_deref(),
        directory: &plan.directory,
        resume: plan.resume.as_deref(),
        new_session: &plan.new_session,
        private,
        codex_keys: &plan.codex_keys,
        opencode_config: opencode_config.as_deref(),
        parent: plan.parent.as_deref(),
        server: Some(&server),
    };
    let Ok(built) = adapter::plan(&request) else {
        return refuse(err, &say("bad_opencode_config", &[]));
    };
    let mut harness = Command::new(program)
        .args(&built.argv[1..])
        .envs(built.env.iter().map(|(key, value)| (key, value)))
        .current_dir(&plan.directory)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    // Only now, so the harness keeps the default disposition: the group's
    // SIGTERM is for everyone but the watcher, which sends it.
    // SAFETY: signal(2) with SIG_IGN installs no handler of ours.
    unsafe {
        signal(SIGTERM, SIG_IGN);
    }
    let mut stdin = harness.stdin.take().expect("piped");
    thread::spawn(move || {
        let _ = stdin.write_all(&prompt);
    });
    let relays = [
        relay(harness.stdout.take().expect("piped"), 1),
        relay(harness.stderr.take().expect("piped"), 2),
    ];
    let (ended, closed) = mpsc::channel::<()>();
    thread::spawn(move || {
        let mut sink = [0u8; 256];
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
    let code = match status {
        Some(status) => {
            leftovers(own);
            code(status)
        }
        None => {
            end_tree(&mut harness, own);
            1
        }
    };
    let drained = Instant::now() + Duration::from_millis(500);
    for handle in relays {
        while !handle.is_finished() && Instant::now() < drained {
            thread::sleep(Duration::from_millis(10));
        }
    }
    Ok(code)
}

/// The group's members other than the watcher, and their descendants.
fn group(rows: &[tree::Row], own: i32) -> BTreeSet<i32> {
    let members: Vec<i32> = tree::members(rows, own)
        .into_iter()
        .filter(|pid| *pid != own)
        .collect();
    tree::descendants(rows, &members)
}

/// End the running harness and everything it started that can be found.
fn end_tree(harness: &mut Child, own: i32) {
    let pid = harness.id() as i32;
    let rows = tree::table();
    let mut first = tree::descendants(&rows, &[pid]);
    first.extend(group(&rows, own));
    tree::signal_all(&rows, &first, own, Some(pid), SIGTERM);
    tree::signal(-own, SIGTERM);
    let deadline = Instant::now() + GRACE;
    while Instant::now() < deadline {
        let gone = !matches!(harness.try_wait(), Ok(None));
        if gone && !first.iter().any(|p| tree::exists(*p)) {
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let rows = tree::table();
    let mut left: BTreeSet<i32> = first.into_iter().filter(|p| tree::exists(*p)).collect();
    left.extend(group(&rows, own));
    let roots: Vec<i32> = left.iter().copied().collect();
    left.extend(tree::descendants(&rows, &roots));
    tree::signal_all(&rows, &left, own, None, SIGKILL);
    let _ = harness.kill();
    let _ = harness.wait();
}

/// After a normal exit: late work in the group may finish, then what
/// remains is ended.
fn leftovers(own: i32) {
    let deadline = Instant::now() + LATE;
    let mut rows = tree::table();
    while !group(&rows, own).is_empty() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
        rows = tree::table();
    }
    let left = group(&rows, own);
    if left.is_empty() {
        return;
    }
    tree::signal_all(&rows, &left, own, None, SIGTERM);
    let deadline = Instant::now() + Duration::from_secs(1);
    while Instant::now() < deadline && left.iter().any(|p| tree::exists(*p)) {
        thread::sleep(Duration::from_millis(50));
    }
    let rows = tree::table();
    let mut last = group(&rows, own);
    last.extend(left.into_iter().filter(|p| tree::exists(*p)));
    tree::signal_all(&rows, &last, own, None, SIGKILL);
}
