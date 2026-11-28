//! One bounded run of a client's own command (X4f Q6), shared by the
//! sign-in status (`status.rs`) and Codex's metadata commands (`probe.rs`):
//! no standard input, each stream read to a byte cap and the rest dropped,
//! in a process group of its own. When the command exits its group is ended,
//! so a helper it left holding a stream closes it; at the deadline the
//! command, its descendants and their groups are ended. The streams are then
//! waited for at most `DRAIN` and never joined beyond it, so a helper that
//! left the group still cannot hold the server.

use std::collections::BTreeSet;
use std::io::Read;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use super::tree::{self, SIGKILL};

/// How long the streams are waited for once the command is over.
const DRAIN: Duration = Duration::from_millis(500);

/// What one run answered.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Ran {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

/// Why a run gave no answer.
#[derive(Debug, PartialEq, Eq)]
pub enum Unanswered {
    Unstartable(String),
    Timeout,
}

/// Run `command` within `bound`, keeping at most `cap` bytes of each stream.
pub fn run(mut command: Command, bound: Duration, cap: usize) -> Result<Ran, Unanswered> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|error| Unanswered::Unstartable(error.to_string()))?;
    let leader = i32::try_from(child.id()).unwrap_or(0);
    let (sent, received) = mpsc::channel();
    keep(child.stdout.take(), 0, cap, sent.clone());
    keep(child.stderr.take(), 1, cap, sent);
    let deadline = Instant::now() + bound;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            _ => break None,
        }
    };
    let Some(status) = status else {
        // The leader is unreaped, so its pid and group are still its own.
        let rows = tree::table();
        let found: BTreeSet<i32> = tree::descendants(&rows, &[leader]);
        tree::signal_all(&rows, &found, std::process::id() as i32, None, SIGKILL);
        tree::signal(-leader, SIGKILL);
        let _ = child.kill();
        let _ = child.wait();
        return Err(Unanswered::Timeout);
    };
    // What it left in its group goes, and with it any stream it held.
    tree::signal(-leader, SIGKILL);
    let mut streams = [String::new(), String::new()];
    let until = Instant::now() + DRAIN;
    for _ in 0..2 {
        let left = until.saturating_duration_since(Instant::now());
        match received.recv_timeout(left) {
            Ok((index, text)) => streams[index] = text,
            Err(_) => break,
        }
    }
    let [stdout, stderr] = streams;
    Ok(Ran {
        code: status.code(),
        stdout,
        stderr,
    })
}

/// Read one stream to its end, keeping its first `cap` bytes.
fn keep(
    stream: Option<impl Read + Send + 'static>,
    index: usize,
    cap: usize,
    sent: mpsc::Sender<(usize, String)>,
) {
    thread::spawn(move || {
        let mut kept = Vec::new();
        if let Some(mut stream) = stream {
            let mut chunk = [0u8; 8192];
            while let Ok(n) = stream.read(&mut chunk) {
                if n == 0 {
                    break;
                }
                let room = cap.saturating_sub(kept.len());
                kept.extend_from_slice(&chunk[..n.min(room)]);
            }
        }
        let _ = sent.send((index, String::from_utf8_lossy(&kept).into_owned()));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_helper_holding_the_output_never_holds_the_run() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "sleep 30 & echo answered"]);
        let started = Instant::now();
        let ran = run(command, Duration::from_secs(5), 64).unwrap();
        assert_eq!(ran.stdout.trim(), "answered");
        assert!(
            started.elapsed() < Duration::from_secs(3),
            "{:?}",
            started.elapsed()
        );
    }

    #[test]
    fn every_stream_is_capped_and_a_slow_command_is_ended() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "yes | head -c 100000; yes | head -c 100000 >&2"]);
        let ran = run(command, Duration::from_secs(5), 1000).unwrap();
        assert_eq!((ran.stdout.len(), ran.stderr.len()), (1000, 1000));
        let mut slow = Command::new("/bin/sh");
        slow.args(["-c", "sleep 30"]);
        let started = Instant::now();
        assert_eq!(
            run(slow, Duration::from_millis(300), 64),
            Err(Unanswered::Timeout)
        );
        assert!(started.elapsed() < Duration::from_secs(3));
    }
}
