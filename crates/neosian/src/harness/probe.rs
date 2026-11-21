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
use super::adapter::bare_key;
use super::codex::{KEYS, keys, server_names};
use super::text::say;

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

/// Why the other servers could not be switched off; each refuses
/// `read-only`, so no server is left on unseen.
#[derive(Debug, PartialEq, Eq)]
pub enum Others {
    /// `codex mcp list --json` did not answer with a list.
    Unreadable(String),
    /// A server name an override cannot address.
    Unaddressable(String),
    /// An entry without a usable name, by its position in the list (#432).
    Unnamed(usize),
}

impl Others {
    /// The refusal, from the asset.
    pub fn sentence(&self) -> String {
        match self {
            Self::Unreadable(error) => say("codex_list_failed", &[("error", error)]),
            Self::Unaddressable(name) => say("codex_unswitchable", &[("name", name)]),
            Self::Unnamed(at) => say("codex_unnamed", &[("entry", &at.to_string())]),
        }
    }
}

/// The configured servers other than neosian's keys, which a read-only
/// child runs with switched off (#431).
pub fn codex_others(program: &Path, dir: &Path) -> Result<Vec<String>, Others> {
    let (ok, text) =
        answer(program, &["mcp", "list", "--json"], dir).map_err(Others::Unreadable)?;
    let listed = server_names(&text)
        .filter(|_| ok)
        .ok_or_else(|| Others::Unreadable(text.trim().chars().take(200).collect()))?;
    let mut others = Vec::new();
    for server in listed {
        match server {
            Ok(name) if KEYS.contains(&name.as_str()) => {}
            Ok(name) if bare_key(&name) => others.push(name),
            Ok(name) => return Err(Others::Unaddressable(name)),
            Err(at) => return Err(Others::Unnamed(at)),
        }
    }
    Ok(others)
}
