//! The sign-in check before a turn (X4c): the client's own status command
//! (`signin::command`) run by the server, outside any sandbox, in the
//! child's directory and the server's environment, so the answer is the
//! one the person would get, less the store credentials no child receives
//! (X4e P4): a status command may refresh and save its sign-in, which the
//! policy would refuse. It is bounded to 5 seconds and 8 KiB per
//! stream, reads nothing from standard input, and runs in a process group
//! of its own that is killed whole when it does not answer in time. Its
//! output is read and dropped: only `signin::classify` sees it, and nothing
//! of it is stored.

use std::io::Read;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use super::adapter::Agent;
use super::sandbox::Launch;
use super::signin::{self, Status};
use super::text::say;
use super::tree::{self, SIGKILL};

/// How long a status command has to answer.
pub const BOUND: Duration = Duration::from_secs(5);
/// How much of each stream is kept.
const KEPT: usize = 8 * 1024;
/// How long the streams are waited for after the command exits: a helper
/// it left holding them never holds the turn.
const DRAIN: Duration = Duration::from_millis(500);

/// What the client's status command says, or None for a client without one.
pub fn check(agent: Agent, program: &Path, dir: &Path, launch: &Launch) -> Option<Status> {
    let words = signin::command(agent)?;
    Some(match run(program, words, dir, launch) {
        Ok((code, stdout, stderr)) => signin::classify(agent, code, &stdout, &stderr),
        Err(reason) => Status::Unknown(reason),
    })
}

/// The exit code and both streams of one bounded run, or why there is none.
fn run(
    program: &Path,
    words: &[&str],
    dir: &Path,
    launch: &Launch,
) -> Result<(Option<i32>, String, String), String> {
    let mut command = Command::new(program);
    launch.strip(&mut command);
    let mut child = command
        .args(words)
        .current_dir(dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|error| say("signin_unstartable", &[("error", &error.to_string())]))?;
    let (sent, received) = mpsc::channel();
    keep(child.stdout.take(), 0, sent.clone());
    keep(child.stderr.take(), 1, sent);
    let deadline = Instant::now() + BOUND;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            outcome => {
                // The whole group, while its leader is still unreaped, so
                // the group ID is still this command's.
                let pgid = i32::try_from(child.id()).unwrap_or(0);
                if pgid > 0 {
                    tree::signal(-pgid, SIGKILL);
                }
                let _ = child.kill();
                let _ = child.wait();
                return Err(match outcome {
                    Err(error) => say("signin_unstartable", &[("error", &error.to_string())]),
                    _ => {
                        let seconds = BOUND.as_secs().to_string();
                        say("signin_timeout", &[("seconds", &seconds)])
                    }
                });
            }
        }
    };
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
    Ok((status.code(), stdout, stderr))
}

/// Read one stream to its end, keeping its first `KEPT` bytes.
fn keep(
    stream: Option<impl Read + Send + 'static>,
    index: usize,
    sent: mpsc::Sender<(usize, String)>,
) {
    thread::spawn(move || {
        let mut kept = Vec::new();
        if let Some(mut stream) = stream {
            let mut chunk = [0u8; 4096];
            while let Ok(n) = stream.read(&mut chunk) {
                if n == 0 {
                    break;
                }
                let room = KEPT.saturating_sub(kept.len());
                kept.extend_from_slice(&chunk[..n.min(room)]);
            }
        }
        let _ = sent.send((index, String::from_utf8_lossy(&kept).into_owned()));
    });
}
