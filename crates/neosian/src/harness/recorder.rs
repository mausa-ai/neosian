//! One call of the record verb for the watcher's filing (X4a): the
//! running executable's `record` with this server's store selection,
//! `--project` the child's directory and `--json`, one hook payload on its
//! standard input, the parent and session in the variables its hooks read,
//! SIGTERM back to its default, and a bound on how long it may take.

use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::Value;

use super::adapter::{PARENT_ENV, SESSION_ENV};
use super::plan::Checked;
use super::text::say;

/// The longest one call of the record verb may take.
const CALL: Duration = Duration::from_secs(30);
/// How much of the verb's output is read.
const OUTPUT_BYTES: usize = 64 << 10;

/// Read at most `OUTPUT_BYTES` of a stream, then drain it.
fn reader(stream: Option<impl Read + Send + 'static>) -> JoinHandle<String> {
    thread::spawn(move || {
        let Some(mut stream) = stream else {
            return String::new();
        };
        let mut kept = Vec::new();
        let mut chunk = [0u8; 8192];
        while let Ok(n) = stream.read(&mut chunk) {
            if n == 0 {
                break;
            }
            let room = OUTPUT_BYTES.saturating_sub(kept.len());
            kept.extend_from_slice(&chunk[..n.min(room)]);
        }
        String::from_utf8_lossy(&kept).into_owned()
    })
}

/// Why a call of the record verb gave no envelope.
pub enum Failure {
    /// It did not finish within `CALL`; it was ended.
    Slow(String),
    Failed(String),
}

impl Failure {
    pub fn text(&self) -> &str {
        match self {
            Self::Slow(text) | Self::Failed(text) => text,
        }
    }
}

/// One call of the record verb with `payload` on its standard input: its
/// JSON envelope, or why it failed.
pub fn call(plan: &Checked, native: &str, payload: &Value) -> Result<Value, Failure> {
    let exe = std::env::current_exe().map_err(|error| Failure::Failed(error.to_string()))?;
    let mut command = Command::new(exe);
    command
        .args(["record", "--agent", plan.agent.name()])
        .args(&plan.selection)
        .arg("--project")
        .arg(&plan.directory)
        .arg("--json")
        .current_dir(&plan.directory)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env_remove(super::CHILD_ENV);
    match plan.parent.as_deref() {
        Some(parent) => command.env(PARENT_ENV, parent).env(SESSION_ENV, native),
        None => command.env_remove(PARENT_ENV).env_remove(SESSION_ENV),
    };
    super::ending::restore_sigterm(&mut command);
    let mut child = command
        .spawn()
        .map_err(|error| Failure::Failed(error.to_string()))?;
    let body = payload.to_string();
    let mut stdin = child.stdin.take();
    let writer = thread::spawn(move || {
        if let Some(stdin) = stdin.as_mut() {
            let _ = stdin.write_all(body.as_bytes());
        }
    });
    let (out, err) = (reader(child.stdout.take()), reader(child.stderr.take()));
    let deadline = Instant::now() + CALL;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(5)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };
    let _ = writer.join();
    let (out, err) = (
        out.join().unwrap_or_default(),
        err.join().unwrap_or_default(),
    );
    let Some(status) = status else {
        let seconds = CALL.as_secs().to_string();
        return Err(Failure::Slow(say("record_slow", &[("seconds", &seconds)])));
    };
    let envelope = out
        .lines()
        .rev()
        .find_map(|line| serde_json::from_str::<Value>(line).ok());
    match envelope {
        Some(envelope) if status.success() => Ok(envelope),
        Some(envelope) if envelope["error"].is_string() => Err(Failure::Failed(
            envelope["error"].as_str().unwrap_or_default().to_string(),
        )),
        _ => Err(Failure::Failed(err.trim().to_string())),
    }
}
