//! Which of the server's keys Codex has (§40.50 item 7): `codex mcp get
//! KEY --json` for each, bounded, in the child's directory and the
//! server's environment, so project layers and CODEX_HOME are Codex's own. An override
//! on a key Codex does not have would stop Codex from starting.

use std::io::Read;
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use super::adapter::CodexKey;
use super::codex::{KEYS, keys};

const BOUND: Duration = Duration::from_secs(5);

/// Exit status and stdout of one bounded command, or why it did not answer.
fn answer(program: &Path, args: &[&str], dir: &Path) -> Result<(bool, String), String> {
    let mut child = Command::new(program)
        .args(args)
        .current_dir(dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| error.to_string())?;
    let mut stdout = child.stdout.take().expect("piped");
    let reader = thread::spawn(move || {
        let mut text = String::new();
        let _ = stdout.read_to_string(&mut text);
        text
    });
    let deadline = Instant::now() + BOUND;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("no answer within {} seconds", BOUND.as_secs()));
            }
            Err(error) => return Err(error.to_string()),
        }
    };
    Ok((status.success(), reader.join().unwrap_or_default()))
}

/// The keys Codex has, with the tools each already disables. A key Codex
/// reports missing (exit 1) is skipped; a probe that cannot run fails the
/// start, since its denial could not be planned.
pub fn codex_keys(program: &Path, dir: &Path) -> Result<Vec<CodexKey>, (String, String)> {
    let mut found = Vec::new();
    for key in KEYS {
        match answer(program, &["mcp", "get", key, "--json"], dir) {
            Ok((true, text)) => found.push((key, text)),
            Ok((false, _)) => {}
            Err(error) => return Err((key.to_string(), error)),
        }
    }
    Ok(keys(&found))
}
