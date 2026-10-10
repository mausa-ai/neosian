//! The operator verbs (`memory/cli_operate.py`, DESIGN §14.2): keyless
//! audit and remedy acts over the store, never commands a model can reach.
//! Each prints its own envelope under `--json`; a failure prints one
//! minimal `{"error", "hint"}` object, so a caller that asked for JSON
//! reads exactly one object either way.

use std::io::{self, Write};

use serde_json::{Value, json};

use super::commands::virtual_path;
use super::dispatch::{Memory, Outcome, corrective};
use super::mounts::resolve;
use crate::store::error::{Error, MemoryError};

/// A verb's exit code, its store refusals rendered as tier-one failures.
pub fn settle(
    result: Result<u8, Error>,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    match result {
        Ok(code) => Ok(code),
        Err(Error::Memory(error)) => fail(&corrective(&error), json, out, err),
        Err(Error::Io(error)) => Err(error),
    }
}

/// Tier one: the guidance on stderr, and under `--json` the one object.
pub fn fail(
    outcome: &Outcome,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let error = outcome.result.as_ref().err();
    if json {
        writeln!(out, "{}", json!({"error": error, "hint": outcome.reminder}))?;
    }
    if let Some(error) = error {
        writeln!(err, "error: {error}")?;
    }
    if let Some(hint) = outcome.reminder.as_deref().filter(|hint| !hint.is_empty()) {
        writeln!(err, "hint: {hint}")?;
    }
    Ok(1)
}

/// A document's version rows, newest first. The text form is the audit
/// trail without content; `--json` carries every row's content, which is
/// how a point-in-time read is made. No rows is an answer, not an error.
pub fn versions(
    memory: &Memory,
    path: &str,
    limit: usize,
    json: bool,
    out: &mut impl Write,
) -> Result<u8, Error> {
    let (mount, document) = resolve(&memory.mounts, path)?;
    if document.is_empty() {
        return Err(MemoryError::path_invalid(path, "a mount root is not a document").into());
    }
    let rows = memory.store.versions(&mount.scope, document, limit)?;
    let shown = virtual_path(mount, document);
    if json {
        let versions: Vec<Value> = rows
            .iter()
            .map(|row| {
                json!({
                    "version": row.version,
                    "action": row.action.as_str(),
                    "actor": row.actor,
                    "created_at": row.created_at.iso(),
                    "redacted": row.redacted,
                    "content": row.content,
                })
            })
            .collect();
        writeln!(out, "{}", json!({"path": shown, "versions": versions}))?;
        return Ok(0);
    }
    if rows.is_empty() {
        writeln!(out, "no history for {shown}")?;
    }
    for row in &rows {
        let actor = row.actor.as_deref().filter(|actor| !actor.is_empty());
        let marker = if row.redacted { " (redacted)" } else { "" };
        let (action, at) = (row.action.as_str(), row.created_at.iso());
        writeln!(
            out,
            "v{}  {action}  {}  {at}{marker}",
            row.version,
            actor.unwrap_or("-")
        )?;
    }
    Ok(0)
}
