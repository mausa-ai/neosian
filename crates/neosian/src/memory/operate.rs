//! The operator verbs (`memory/cli_operate.py`, DESIGN §14.2): keyless
//! audit and remedy acts over the store, never commands a model can reach.
//! Each prints its own envelope under `--json`; a failure prints one
//! minimal `{"error", "hint"}` object, so a caller that asked for JSON
//! reads exactly one object either way.

use std::io::{self, Write};

use serde_json::{Value, json};

use super::commands::{document_at, virtual_path};
use super::dispatch::{Call, Memory, Outcome, corrective, dispatch};
use super::index::count;
use super::mounts::{resolve, writable};
use crate::repr::repr;
use crate::store::error::{Error, MemoryError};
use crate::store::journal::Action;

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
        Err(Error::Value(sentence)) => fail(&Outcome::fail(sentence), json, out, err),
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
    let (mount, document) = document_at(memory, path)?;
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

/// Clear one document's content everywhere, or with `scope_wide` a whole
/// mount's scope: the one irreversible verb. A read-only mount refuses; an
/// edit-only one allows it, since clearing content leaves the document
/// set as it was.
pub fn redact(
    memory: &Memory,
    store: &crate::store::FileStore,
    path: &str,
    scope_wide: bool,
    json: bool,
    out: &mut impl Write,
) -> Result<u8, Error> {
    let (mount, document) = resolve(&memory.mounts, path)?;
    writable(mount)?;
    let target = (!scope_wide).then_some(document);
    let matched = store.redact(&mount.scope, target, &memory.actor)?;
    if json {
        let envelope = json!({"path": path, "scope_wide": scope_wide, "matched": matched});
        writeln!(out, "{envelope}")?;
    } else if scope_wide {
        // Two mounts on one scope share the erasure, so the scope is named.
        let (cleared, scope) = (count(matched), repr(&mount.scope));
        writeln!(
            out,
            "redacted {cleared} in scope {scope} (mount /{})",
            mount.path
        )?;
    } else {
        let (cleared, shown) = (count(matched), virtual_path(mount, document));
        writeln!(out, "redacted {cleared} at {shown}")?;
    }
    Ok(0)
}

/// Undo the write that produced `version` of a document (`memory/
/// revert.py`): with no live document before that row the inverse is a
/// delete, otherwise the row before it comes back. The inverse runs
/// through the ladder, so it appends a row and never rewrites history.
/// The target must be the newest row, and redacted history refuses: its
/// skeleton is deliberately not restorable.
fn undo(memory: &Memory, path: &str, version: u64) -> Result<Outcome, Error> {
    let (mount, document) = document_at(memory, path)?;
    writable(mount)?;
    let rows = memory.store.versions(&mount.scope, document, 2)?;
    let shown = virtual_path(mount, document);
    let Some(newest) = rows.first() else {
        return Ok(Outcome::fail(format!("No history for {shown}"))
            .hint("List documents with the 'view' command and path '/' first."));
    };
    if newest.version != version {
        return Err(MemoryError::conflict(&mount.scope, Some(document), "revert_stale").into());
    }
    let prior = rows.get(1).filter(|row| row.action != Action::Deleted);
    if newest.redacted || prior.is_some_and(|row| row.redacted) {
        let cleared = "history is redacted; the prior content was cleared and cannot be restored";
        return Ok(Outcome::fail(format!("{shown}'s {cleared}"))
            .hint("Create the document again if it is still needed."));
    }
    let (inverse, prose) = match prior {
        Some(row) => (
            Call::Create {
                path,
                content: &row.content,
            },
            format!("Reverted {shown} to v{}'s content", row.version),
        ),
        None => (
            Call::Delete { path },
            format!("Reverted {shown}: v{version} undone — the document is removed"),
        ),
    };
    let undone = dispatch(memory, &inverse)?;
    // The ladder's own prose, and create's overwrite reminder, are noise
    // on an undo: the answer is rewritten around the receipt.
    Ok(match undone.receipt {
        Some(receipt) if undone.result.is_ok() => {
            let prose = format!("{prose} (now v{})", receipt.version);
            Outcome::ok(prose).wrote(receipt.path, receipt.version, receipt.previous_path)
        }
        _ => undone,
    })
}

/// `revert`: the undo on the streams, its envelope the receipt's fields.
pub fn revert(
    memory: &Memory,
    path: &str,
    version: u64,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> Result<u8, Error> {
    let undone = undo(memory, path, version)?;
    let Ok(detail) = &undone.result else {
        return Ok(fail(&undone, json, out, err)?);
    };
    if json {
        let receipt = undone.receipt.as_ref();
        let envelope = json!({
            "command": "revert",
            "path": receipt.map(|receipt| &receipt.path),
            "version": receipt.map(|receipt| receipt.version),
            "previous_path": receipt.and_then(|receipt| receipt.previous_path.as_ref()),
            "detail": detail,
        });
        writeln!(out, "{envelope}")?;
    } else {
        writeln!(out, "{detail}")?;
    }
    Ok(0)
}
