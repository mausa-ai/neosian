//! Claude Code's entries moved through its own CLI, adding first (DESIGN
//! §40.53 item 1, from `phase/x3-rename`): `add-json` with the entry on
//! standard input, every member read back, the receipt recorded, then the old
//! entry removed and read back. Local scope runs in the project's directory.

use super::{jsonedit, text};
use crate::clients::{
    context::Context,
    files::Failure,
    naming::NEW,
    receipt, runner,
    targets::{self, Document},
};
use serde_json::Value;
use std::path::{Path, PathBuf};

pub const AFTER_FAILURE: &str = "fix the cause, then run neosian setup --refresh";

/// User scope, or the local scope of a project (its key in `.claude.json`,
/// also the directory the CLI runs in).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Scope {
    User,
    Local(String),
}

impl Scope {
    pub fn pointer(&self, key: &str) -> String {
        match self {
            Self::User => jsonedit::join(&["mcpServers", key]),
            Self::Local(project) => jsonedit::join(&["projects", project, "mcpServers", key]),
        }
    }

    fn name(&self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Local(_) => "local",
        }
    }

    fn project(&self) -> Option<PathBuf> {
        match self {
            Self::User => None,
            Self::Local(project) => Some(PathBuf::from(project)),
        }
    }
}

pub fn file(context: &Context) -> PathBuf {
    targets::mcp("claude-code", context, "user").path
}

fn claude(
    context: &Context,
    scope: &Scope,
    words: &[&str],
    input: Option<&str>,
) -> Result<(), String> {
    let argv: Vec<String> = std::iter::once("claude")
        .chain(words.iter().copied())
        .map(str::to_owned)
        .collect();
    match runner::run_in(&argv, &context.env, input, scope.project().as_deref()) {
        Some((true, _)) => Ok(()),
        Some((false, error)) => Err(error),
        None => Err(text::get("cli_missing")),
    }
}

fn add(context: &Context, scope: &Scope, name: &str, entry: &Value) -> Result<(), String> {
    let json = crate::pyfmt::dumps_spaced(entry, true, false);
    let words = ["mcp", "add-json", "--scope", scope.name(), name, "-"];
    claude(context, scope, &words, Some(&json))
}

fn remove(context: &Context, scope: &Scope, name: &str) -> Result<(), String> {
    claude(
        context,
        scope,
        &["mcp", "remove", "--scope", scope.name(), name],
        None,
    )
}

/// The entry under `key` in that scope, as Claude reads the file now.
pub fn entry(context: &Context, scope: &Scope, key: &str) -> Result<Option<Value>, String> {
    let path = file(context);
    match targets::document("claude-code", &path) {
        Document::Absent => Ok(None),
        Document::Read(document) => Ok(document.pointer(&scope.pointer(key)).cloned()),
        Document::Unreadable => Err(text::fill(
            "unreadable",
            &[("path", &path.display().to_string())],
        )),
    }
}

fn failure(message: String) -> Failure {
    Failure::new(message, AFTER_FAILURE)
}

/// Every member of `planned` is in `written` with the same value.
fn carried(planned: &Value, written: &Value) -> Vec<String> {
    planned
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(k, v)| written.get(*k) != Some(*v))
        .map(|(k, _)| k.clone())
        .collect()
}

/// `from` moved to `to` in that scope: added, read back, recorded (when `to`
/// is the new key), then `from` removed and read back.
pub fn moved(
    context: &Context,
    scope: &Scope,
    planned: &Value,
    from: &str,
    to: &str,
) -> Result<(), Failure> {
    let path = file(context);
    if entry(context, scope, from).map_err(failure)?.as_ref() != Some(planned) {
        return Err(failure(text::fill(
            "changed",
            &[("path", &path.display().to_string())],
        )));
    }
    if to == NEW {
        receipt::pending("claude-code", context, &path, &scope.pointer(NEW), planned)?;
    }
    add(context, scope, to, planned)
        .map_err(|e| failure(text::fill("cli_failed", &[("error", &e)])))?;
    let written = entry(context, scope, to)
        .map_err(failure)?
        .unwrap_or(Value::Null);
    let lost = carried(planned, &written);
    if !lost.is_empty() {
        let _ = remove(context, scope, to);
        let members = lost.join(", ");
        return Err(failure(text::fill("cli_lost", &[("members", &members)])));
    }
    if to == NEW {
        receipt::record("claude-code", context, &path, &scope.pointer(NEW), &written)?;
    }
    removed(context, scope, from)
}

/// `key` removed in that scope and confirmed gone.
pub fn removed(context: &Context, scope: &Scope, key: &str) -> Result<(), Failure> {
    remove(context, scope, key)
        .and_then(|()| match entry(context, scope, key)? {
            Some(_) => Err(text::get("cli_still_listed")),
            None => Ok(()),
        })
        .map_err(|e| failure(text::fill("cli_failed", &[("error", &e)])))
}

/// `entry` added under `key` where it is absent (a step put back).
pub fn restored(
    context: &Context,
    scope: &Scope,
    key: &str,
    entry_value: &Value,
) -> Result<(), String> {
    if entry(context, scope, key)?.is_some() {
        return Ok(());
    }
    add(context, scope, key, entry_value)
}

/// Whether the CLI keys this project's local scope by the project itself
/// (2.1.330 keys it by the repository root).
pub fn addressable(project: &str) -> bool {
    let path = Path::new(project);
    path.is_dir()
        && path.canonicalize().is_ok_and(|real| real == path)
        && path
            .ancestors()
            .find(|d| d.join(".git").exists())
            .is_none_or(|root| root == path)
}
