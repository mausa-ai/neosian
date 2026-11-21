//! What the server hands a turn's watcher (§40.50 item 3, amended by #430
//! and #431): a plan, never a command. The watcher checks every field again
//! with exactly the tool's rules and builds the argument vector itself
//! through the same adapter, so `neosian harness-watch` can start only one
//! of the three harnesses with its presets, and only where the tool is
//! enabled, outside a child, with a home directory, in a directory the tool
//! itself would allow.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::adapter::{self, Agent, Mode};
use super::text::say;
use crate::memory::mounts::Mount;

/// The longest plan line read.
pub const PLAN_BYTES: usize = 64 * 1024;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WirePlan {
    pub agent: String,
    pub mode: String,
    pub directory: String,
    pub prompt_bytes: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resume: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_session: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent: Option<String>,
    /// The server's store selection and mounts, as `neosian mcp` flags.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub store: Vec<String>,
    /// The token that marks the watcher's own exit line on stderr.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nonce: Option<String>,
}

/// A plan whose every field was checked.
#[derive(Debug)]
pub struct Checked {
    pub agent: Agent,
    pub mode: Mode,
    pub directory: PathBuf,
    pub model: Option<String>,
    pub resume: Option<String>,
    pub new_session: String,
    pub parent: Option<String>,
    /// Store selection flags (`--root`, `--url`, `--schema`, `--profile`).
    pub selection: Vec<String>,
    /// Mount tokens, each made read-only for a read-only child.
    pub mounts: Vec<String>,
    pub nonce: String,
}

/// An http(s) URL with no whitespace and no user information (#432): a
/// read-only child's command line carries the URL, and a credential there
/// would show in the process table. The authority ends at the first `/`,
/// `?` or `#`; any `@` before it is user information.
fn url_ok(value: &str) -> bool {
    let rest = value
        .strip_prefix("https://")
        .or_else(|| value.strip_prefix("http://"));
    let Some(rest) = rest else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    !value.contains(char::is_whitespace) && !authority.contains('@')
}

/// Store flags in the forms `neosian mcp` accepts: each once (`--mount`
/// repeatable), `--root` absolute, `--url` http(s) without user
/// information, a valid schema, profile name, scope or mount, not `--root`
/// with `--url`, not `--scope` with `--mount`. Returns the selection flags
/// and the mount tokens.
pub fn store(flags: &[String]) -> Option<(Vec<String>, Vec<String>)> {
    if !flags.len().is_multiple_of(2) {
        return None;
    }
    let mut seen = BTreeSet::new();
    let (mut selection, mut mounts) = (Vec::new(), Vec::new());
    for pair in flags.chunks(2) {
        let (flag, value) = (pair[0].as_str(), pair[1].as_str());
        if value.is_empty() || value.contains('\0') || (flag != "--mount" && !seen.insert(flag)) {
            return None;
        }
        let ok = match flag {
            "--root" => Path::new(value).is_absolute(),
            "--url" => url_ok(value),
            "--schema" => crate::postgres::validate_schema(value).is_ok(),
            "--profile" => crate::profile::validate::name(value).is_ok(),
            "--scope" => crate::store::grammar::check_scope(value).is_ok(),
            "--mount" => Mount::parse(value).is_ok(),
            _ => false,
        };
        if !ok {
            return None;
        }
        match flag {
            "--mount" => mounts.push(value.to_string()),
            "--scope" => mounts.push(format!("scope={value},path=memories")),
            _ => selection.extend([flag.to_string(), value.to_string()]),
        }
    }
    let both = |a: &str, b: &str| seen.contains(a) && seen.contains(b);
    let scoped_and_mounted = seen.contains("--scope") && flags.iter().any(|f| f == "--mount");
    if both("--root", "--url") || scoped_and_mounted {
        return None;
    }
    Some((selection, mounts))
}

/// A mount token made read-only: its access word replaced by `ro`.
pub fn read_only(token: &str) -> String {
    let parts: Vec<&str> = token
        .split(',')
        .filter(|part| !matches!(*part, "ro" | "eo"))
        .collect();
    format!("{},ro", parts.join(","))
}

/// Check a plan as the watcher reads it, with the tool's own rules.
pub fn check(plan: WirePlan, home: Option<&Path>, listed: &[PathBuf]) -> Result<Checked, String> {
    let bad = |field: &str| say("watch_bad_field", &[("field", field)]);
    let Some(home) = home.filter(|home| !home.as_os_str().is_empty()) else {
        return Err(bad("HOME"));
    };
    let agent = Agent::parse(&plan.agent).ok_or_else(|| bad("agent"))?;
    let mode = Mode::parse(&plan.mode).ok_or_else(|| bad("mode"))?;
    if !(1..=adapter::PROMPT_BYTES).contains(&plan.prompt_bytes) {
        return Err(bad("prompt_bytes"));
    }
    if plan.model.as_deref().is_some_and(|m| !adapter::model_ok(m)) {
        return Err(bad("model"));
    }
    if plan
        .resume
        .as_deref()
        .is_some_and(|s| !adapter::session_ok(s))
    {
        return Err(bad("resume"));
    }
    let new_session = match plan.new_session {
        Some(session) => uuid::Uuid::parse_str(&session)
            .map_err(|_| bad("new_session"))?
            .to_string(),
        None => uuid::Uuid::new_v4().to_string(),
    };
    let parent_ok = |p: &str| crate::conversation::ids::parse_conversation_id(p).is_ok();
    if plan.parent.as_deref().is_some_and(|p| !parent_ok(p)) {
        return Err(bad("parent"));
    }
    let (selection, mounts) = store(&plan.store).ok_or_else(|| bad("store"))?;
    let nonce = match plan.nonce {
        Some(nonce) if nonce.len() == 32 && nonce.chars().all(|c| c.is_ascii_hexdigit()) => nonce,
        Some(_) => return Err(bad("nonce")),
        None => uuid::Uuid::new_v4().simple().to_string(),
    };
    let directory = Path::new(&plan.directory);
    if !directory.is_absolute() {
        return Err(bad("directory"));
    }
    // From the watcher, the directory is "the server's own": the same rule.
    let directory = adapter::place(None, directory, Some(home), listed)
        .or_else(|_| adapter::place(Some(&plan.directory), directory, Some(home), listed))
        .map_err(|_| bad("directory"))?;
    Ok(Checked {
        agent,
        mode,
        directory,
        model: plan.model,
        resume: plan.resume,
        new_session,
        parent: plan.parent,
        selection,
        mounts,
        nonce,
    })
}

#[cfg(test)]
mod tests;
