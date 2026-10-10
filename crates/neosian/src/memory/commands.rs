//! The memory commands' bodies (`memory/commands.py`, DESIGN §8): what a
//! model, an operator or a shell asks of the mounts, answered as data or
//! as a failure it can act on. Store refusals leave as errors; the ladder
//! in `dispatch.rs` makes them corrective.

use super::dispatch::{Memory, Outcome};
use super::index;
use super::mounts::{Mount, resolve};
use crate::store::error::Error;
use crate::store::{Document, Entry};

const REDACTED_NOTICE: &str = "(redacted — content was cleared; history is preserved)";
const INDEX_HINT: &str = "Run the memory tool with command 'view' and path '/' first.";

pub fn virtual_path(mount: &Mount, document: &str) -> String {
    format!("/{}/{document}", mount.path)
}

/// A document with its lines numbered, the whole of it or one span.
fn numbered(document: &Document, shown: &str, span: Option<(usize, usize)>) -> String {
    let lines: Vec<&str> = document.content.split('\n').collect();
    let width = lines.len().to_string().len();
    let (start, end) = span.unwrap_or((1, lines.len()));
    let body: Vec<String> = (start..=end)
        .map(|number| format!("{number:>width$}: {}", lines[number - 1]))
        .collect();
    format!(
        "{shown} (v{}):\n{}",
        document.envelope.version,
        body.join("\n")
    )
}

/// A 1-based inclusive span from `[start, end]`, `end` -1 meaning the last
/// line; or the sentence that says why not.
fn view_span((start, end): (i64, i64), lines: i64) -> Result<(usize, usize), String> {
    if start < 1 || start > lines {
        return Err(format!("view_range start {start} is outside [1, {lines}]"));
    }
    if end != -1 && end < start {
        return Err(format!(
            "view_range end {end} must be -1 or >= start {start}"
        ));
    }
    let end = if end == -1 { lines } else { end.min(lines) };
    Ok((start as usize, end as usize))
}

fn listing(mount: &Mount, entries: &[Entry]) -> Outcome {
    let shown: Vec<String> = entries
        .iter()
        .map(|entry| {
            let marker = if entry.redacted { " (redacted)" } else { "" };
            format!("- {}{marker}", virtual_path(mount, &entry.path))
        })
        .collect();
    if shown.is_empty() {
        return Outcome::ok(format!("/{} is empty", mount.path));
    }
    Outcome::ok(shown.join("\n"))
}

/// A document, a directory's listing, or `/`: the index. A range applies
/// to a document only.
pub fn view(memory: &Memory, path: &str, range: Option<(i64, i64)>) -> Result<Outcome, Error> {
    if path.trim_matches('/').is_empty() {
        return Ok(Outcome::ok(index::render(&memory.store, &memory.mounts)?));
    }
    let (mount, document) = resolve(&memory.mounts, path)?;
    if document.is_empty() {
        return Ok(listing(
            mount,
            &memory.store.list_documents(&mount.scope, "")?,
        ));
    }
    let shown = virtual_path(mount, document);
    if let Some(found) = memory.store.read(&mount.scope, document)? {
        if found.envelope.redacted {
            return Ok(Outcome::ok(format!("{shown}: {REDACTED_NOTICE}")));
        }
        let lines = found.content.matches('\n').count() as i64 + 1;
        return Ok(
            match range.map(|range| view_span(range, lines)).transpose() {
                Ok(span) => Outcome::ok(numbered(&found, &shown, span)),
                Err(why) => Outcome::fail(why),
            },
        );
    }
    // The prefix is a plain string match: the trailing slash makes it a
    // directory boundary instead of a name prefix.
    let entries = memory
        .store
        .list_documents(&mount.scope, &format!("{document}/"))?;
    if entries.is_empty() {
        return Ok(Outcome::fail(format!("No document or directory at {shown}")).hint(INDEX_HINT));
    }
    Ok(listing(mount, &entries))
}
