//! `neosian harness-watch PROMPT -- ARGV` (hidden; §40.50 item 3): one
//! turn's watcher. The server starts it in a process group of its own with
//! standard input a pipe the server holds. It hands the prompt file to the
//! harness as standard input and unlinks it, starts the harness in its
//! group, and exits with the harness's status. When its standard input
//! ends first (the server stopped the turn, or the server is gone by any
//! cause), it ends the whole group: SIGTERM, a grace period, SIGKILL.

use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{self, Read};
use std::os::unix::process::ExitStatusExt;
use std::path::PathBuf;
use std::process::{Command, ExitStatus};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use clap::Args;

pub const SIGKILL: i32 = 9;
pub const SIGTERM: i32 = 15;
const SIG_IGN: usize = 1;
/// How long the group has to end after SIGTERM.
pub const GRACE: Duration = Duration::from_secs(5);

unsafe extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
    fn signal(signum: i32, handler: usize) -> usize;
}

/// Send `sig` to the process group `group` (a group leader's pid).
pub fn signal_group(group: u32, sig: i32) {
    let Ok(group) = i32::try_from(group) else {
        return;
    };
    if group > 1 {
        // SAFETY: kill(2) with a negative pid addresses one process group;
        // it reads no memory of ours.
        unsafe {
            kill(-group, sig);
        }
    }
}

/// One turn's watcher (hidden; started only by the MCP server).
#[derive(Args)]
pub struct Watch {
    /// The prompt file, unlinked once open.
    prompt: PathBuf,
    /// The harness's argv.
    #[arg(last = true, required = true)]
    argv: Vec<OsString>,
}

fn code(status: ExitStatus) -> u8 {
    match (status.code(), status.signal()) {
        (Some(code), _) => u8::try_from(code).unwrap_or(1),
        (None, Some(signal)) => u8::try_from(128 + signal).unwrap_or(1),
        _ => 1,
    }
}

pub fn run(args: &Watch) -> io::Result<u8> {
    let prompt = File::open(&args.prompt)?;
    let _ = fs::remove_file(&args.prompt);
    let mut harness = Command::new(&args.argv[0])
        .args(&args.argv[1..])
        .stdin(prompt)
        .spawn()?;
    // Only now, so the harness keeps the default disposition: the group's
    // SIGTERM is for everyone but the watcher, which sends it.
    // SAFETY: signal(2) with SIG_IGN installs no handler of ours.
    unsafe {
        signal(SIGTERM, SIG_IGN);
    }
    let (ended, closed) = mpsc::channel::<()>();
    thread::spawn(move || {
        let mut sink = [0u8; 256];
        let mut input = io::stdin().lock();
        while matches!(input.read(&mut sink), Ok(n) if n > 0) {}
        let _ = ended.send(());
    });
    let group = std::process::id();
    loop {
        if let Some(status) = harness.try_wait()? {
            return Ok(code(status));
        }
        match closed.recv_timeout(Duration::from_millis(20)) {
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    signal_group(group, SIGTERM);
    let deadline = Instant::now() + GRACE;
    while Instant::now() < deadline {
        if harness.try_wait()?.is_some() {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    // Whatever is left in the group, the watcher included.
    signal_group(group, SIGKILL);
    Ok(1)
}
