//! Which of the server's registrations Codex has (§40.50 item 7): `codex
//! mcp get KEY --json` for each of neosian's names, and `codex mcp list
//! --json` for every other, in the child's directory and the server's
//! environment, so project layers and CODEX_HOME are Codex's own; on macOS
//! under the turn's prepared policy and without the child's credentials, as
//! the turn itself runs (X4e P4), through the shared bounded runner (X4f
//! Q6). An override on a key Codex does not have would stop Codex from
//! starting. Each entry found is classified by its transport
//! (`transport.rs`, X4c). A stdio registration that runs neosian under any
//! other name is found in the list and gets the same child marker and
//! `harness` filter (X4f Q8). Codex's `get`, `list` and the turn are
//! separate processes: a registration changed between them is not seen.

use std::path::Path;
use std::time::Duration;

use serde_json::Value;

use super::adapter::{CodexKey, Mode, bare_key};
use super::bounded::{self, Unanswered};
use super::codex::{keys, server_names};
use super::sandbox::Launch;
use super::text::say;
use super::transport::{self, KEYS, Transport};

const BOUND: Duration = Duration::from_secs(5);
/// How much of a metadata command's output is read.
const KEPT: usize = 1 << 20;

/// Exit status and stdout of one bounded command, or why it did not answer.
fn answer(
    launch: &Launch,
    program: &Path,
    args: &[&str],
    dir: &Path,
) -> Result<(bool, String), String> {
    let mut command = launch.command(program, args, &[]);
    command.current_dir(dir);
    match bounded::run(command, BOUND, KEPT) {
        Ok(ran) => Ok((ran.code == Some(0), ran.stdout)),
        Err(Unanswered::Unstartable(error)) => Err(error),
        Err(Unanswered::Timeout) => Err(format!("no answer within {} seconds", BOUND.as_secs())),
    }
}

/// The registrations Codex has under neosian's names and, as stdio entries
/// that run neosian, under any other; and in `read-only` every other
/// server, switched off for the run. A probe that cannot run, or an entry
/// whose transport neosian does not know, fails the start with the
/// sentence, since its denial could not be planned.
pub fn codex(
    launch: &Launch,
    program: &Path,
    dir: &Path,
    mode: Mode,
) -> Result<(Vec<CodexKey>, Vec<String>), String> {
    let mut found = codex_keys(launch, program, dir)?;
    let listed = answer(launch, program, &["mcp", "list", "--json"], dir);
    let text = match (listed, mode) {
        (Ok((true, text)), _) => text,
        (Ok((false, text)), Mode::ReadOnly) => {
            return Err(Others::Unreadable(super::signin::scrub(&text)).sentence());
        }
        (Err(error), Mode::ReadOnly) => return Err(Others::Unreadable(error).sentence()),
        // In `write` nothing else is switched off, and an unreadable list
        // leaves the TMPDIR marker as the other names' only layer.
        (_, Mode::Write) => String::new(),
    };
    for name in aliases(&text) {
        if found.iter().all(|key| key.key != name)
            && let Some(Ok(key)) =
                get(launch, program, dir, &name)?.and_then(|answer| transport::key(&name, &answer))
            && key.transport == Transport::Stdio
        {
            found.push(key);
        }
    }
    let off = match mode {
        Mode::Write => Vec::new(),
        Mode::ReadOnly => codex_others(&text, &found).map_err(|others| others.sentence())?,
    };
    Ok((found, off))
}

/// One `codex mcp get NAME --json` answer, None when Codex has no such entry.
fn get(launch: &Launch, program: &Path, dir: &Path, name: &str) -> Result<Option<String>, String> {
    match answer(launch, program, &["mcp", "get", name, "--json"], dir) {
        Ok((true, text)) => Ok(Some(text)),
        Ok((false, _)) => Ok(None),
        Err(error) => Err(say(
            "codex_probe_failed",
            &[("key", name), ("error", &error)],
        )),
    }
}

/// The names of the listed stdio entries whose command is neosian.
pub fn aliases(answer: &str) -> Vec<String> {
    let Ok(Value::Array(servers)) = serde_json::from_str::<Value>(answer) else {
        return Vec::new();
    };
    let own = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.canonicalize().ok());
    let runs_neosian = |command: &str| {
        let path = Path::new(command);
        path.file_stem().is_some_and(|stem| stem == "neosian")
            || (own.is_some() && path.canonicalize().ok() == own)
    };
    servers
        .iter()
        .filter(|entry| transport::transport(entry) == Ok(Transport::Stdio))
        .filter(|entry| {
            let command = entry["transport"]["command"]
                .as_str()
                .or(entry["command"].as_str());
            command.is_some_and(runs_neosian)
        })
        .filter_map(|entry| entry["name"].as_str())
        .filter(|name| bare_key(name))
        .map(str::to_string)
        .collect()
}

/// The keys Codex has under neosian's names, with the tools each already
/// disables and its transport; a key Codex reports missing is skipped.
fn codex_keys(launch: &Launch, program: &Path, dir: &Path) -> Result<Vec<CodexKey>, String> {
    let mut found = Vec::new();
    for key in KEYS {
        if let Some(text) = get(launch, program, dir, key)? {
            found.push((key, text));
        }
    }
    keys(&found).map_err(|(key, transport)| {
        say(
            "codex_transport",
            &[("name", &key), ("transport", &transport)],
        )
    })
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
    /// A server whose transport neosian does not know, with that name (X4c).
    Transport(String, String),
}

impl Others {
    /// The refusal, from the asset.
    pub fn sentence(&self) -> String {
        match self {
            Self::Unreadable(error) => say("codex_list_failed", &[("error", error)]),
            Self::Unaddressable(name) => say("codex_unswitchable", &[("name", name)]),
            Self::Unnamed(at) => say("codex_unnamed", &[("entry", &at.to_string())]),
            Self::Transport(name, transport) => say(
                "codex_transport_other",
                &[("name", name), ("transport", transport)],
            ),
        }
    }
}

/// The configured servers other than the keys found, which a read-only
/// child runs with switched off (#431); a key found is switched off, or
/// not, by its own transport's fields (X4c).
fn codex_others(text: &str, found: &[CodexKey]) -> Result<Vec<String>, Others> {
    let listed = server_names(text)
        // Shown scrubbed: a list may carry an HTTP entry's headers (X4c).
        .ok_or_else(|| Others::Unreadable(super::signin::scrub(text)))?;
    let transports = transport::listed(text);
    let mut others = Vec::new();
    for (server, transport) in listed.into_iter().zip(transports) {
        match server {
            Ok(name) if found.iter().any(|key| key.key == name) => {}
            Ok(name) if !bare_key(&name) => return Err(Others::Unaddressable(name)),
            Ok(name) => match transport {
                Ok(_) => others.push(name),
                Err(unknown) => return Err(Others::Transport(name, unknown)),
            },
            Err(at) => return Err(Others::Unnamed(at)),
        }
    }
    Ok(others)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_stdio_entry_that_runs_neosian_is_found_whatever_its_name() {
        let listed = serde_json::json!([
            {"name": "my-memory", "transport": {"type": "stdio", "command": "/opt/bin/neosian", "args": ["mcp"]}},
            {"name": "playwright", "transport": {"type": "stdio", "command": "npx"}},
            {"name": "hosted", "transport": {"type": "streamable_http", "url": "https://h"}},
            {"name": "bad name", "transport": {"type": "stdio", "command": "neosian"}},
        ]);
        assert_eq!(aliases(&listed.to_string()), ["my-memory"]);
        assert!(aliases("not json").is_empty());
    }
}
