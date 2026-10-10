//! The memory commands' bodies (`memory/commands.py`, DESIGN §8): what a
//! model, an operator or a shell asks of the mounts, answered as data or
//! as a failure it can act on. Store refusals leave as errors; the ladder
//! in `dispatch.rs` makes them corrective.

use super::dispatch::{Memory, Outcome};
use super::index;
use super::mounts::{Mount, resolve, structural, writable};
use crate::store::error::{Error, MemoryError};
use crate::store::{Document, Entry};

const REDACTED_NOTICE: &str = "(redacted — content was cleared; history is preserved)";
const INDEX_HINT: &str = "Run the memory tool with command 'view' and path '/' first.";

pub fn virtual_path(mount: &Mount, document: &str) -> String {
    format!("/{}/{document}", mount.path)
}

/// Resolve a path that must name a document, not a mount's root.
pub fn document_at<'a>(
    memory: &'a Memory,
    path: &'a str,
) -> Result<(&'a Mount, &'a str), MemoryError> {
    let (mount, document) = resolve(&memory.mounts, path)?;
    if document.is_empty() {
        return Err(MemoryError::path_invalid(
            path,
            "a mount root is not a document",
        ));
    }
    Ok((mount, document))
}

fn required(memory: &Memory, mount: &Mount, document: &str) -> Result<Document, Error> {
    let found = memory.store.read(&mount.scope, document)?;
    found.ok_or_else(|| MemoryError::not_found(&mount.scope, document).into())
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

/// Create or overwrite. An overwrite carries the version it read, like
/// every command that destroys content: a document that moved underneath
/// fails correctively instead of being silently replaced.
pub fn create(memory: &Memory, path: &str, content: &str) -> Result<Outcome, Error> {
    let (mount, document) = document_at(memory, path)?;
    writable(mount)?;
    let existing = memory.store.read(&mount.scope, document)?;
    let was = existing.map(|found| found.envelope.version);
    if was.is_none() {
        // Overwriting is an edit; only a brand-new path grows the set.
        structural(mount)?;
    }
    let version = memory
        .store
        .write(&mount.scope, document, content, &memory.actor, was)?;
    let shown = virtual_path(mount, document);
    let outcome = Outcome::ok(format!("Created {shown} (v{version})")).wrote(shown, version, None);
    Ok(match was {
        Some(was) => outcome.hint(format!(
            "Overwrote an existing document (was v{was}); prefer str_replace for edits."
        )),
        None => outcome,
    })
}

fn cleared(shown: &str) -> Outcome {
    Outcome::fail(format!("{shown} is redacted; its content was cleared"))
        .hint("Create the document again if it is still needed.")
}

fn edited(shown: String, version: u64) -> Outcome {
    Outcome::ok(format!("Edited {shown} (v{version})")).wrote(shown, version, None)
}

/// The 1-based line each occurrence of `needle` starts on, an occurrence
/// overlapping the one before included.
fn match_lines(content: &str, needle: &str) -> Vec<usize> {
    let mut lines = Vec::new();
    let mut from = 0;
    while let Some(at) = content[from..].find(needle).map(|at| from + at) {
        lines.push(content[..at].matches('\n').count() + 1);
        from = at + content[at..].chars().next().map_or(1, char::len_utf8);
    }
    lines
}

/// Replace exactly one occurrence: none, or several, is a failure that
/// says where to look.
pub fn str_replace(memory: &Memory, path: &str, old: &str, new: &str) -> Result<Outcome, Error> {
    let (mount, document) = document_at(memory, path)?;
    writable(mount)?;
    if old.is_empty() {
        return Ok(Outcome::fail("old_str must be non-empty"));
    }
    let found = required(memory, mount, document)?;
    let shown = virtual_path(mount, document);
    if found.envelope.redacted {
        return Ok(cleared(&shown));
    }
    let matches = match_lines(&found.content, old);
    if matches.is_empty() {
        return Ok(
            Outcome::fail(format!("old_str did not appear verbatim in {shown}"))
                .hint("View the document and copy the exact text to replace."),
        );
    }
    if matches.len() > 1 {
        let lines: Vec<String> = matches.iter().map(usize::to_string).collect();
        let (times, lines) = (matches.len(), lines.join(", "));
        return Ok(Outcome::fail(format!(
            "old_str appears {times} times in {shown} (lines {lines})"
        ))
        .hint("Include more surrounding text to make it unique."));
    }
    let content = found.content.replacen(old, new, 1);
    let read = Some(found.envelope.version);
    let version = memory
        .store
        .write(&mount.scope, document, &content, &memory.actor, read)?;
    Ok(edited(shown, version))
}

/// Splice `text` in as line `line`, 0 putting it first.
pub fn insert(memory: &Memory, path: &str, line: i64, text: &str) -> Result<Outcome, Error> {
    let (mount, document) = document_at(memory, path)?;
    writable(mount)?;
    let found = required(memory, mount, document)?;
    let shown = virtual_path(mount, document);
    if found.envelope.redacted {
        return Ok(cleared(&shown));
    }
    let mut lines: Vec<&str> = found.content.split('\n').collect();
    let Some(at) = usize::try_from(line).ok().filter(|at| *at <= lines.len()) else {
        let last = lines.len();
        return Ok(Outcome::fail(format!(
            "insert_line {line} is out of range for {shown} (valid: 0 to {last})"
        )));
    };
    lines.insert(at, text);
    let read = Some(found.envelope.version);
    let content = lines.join("\n");
    let version = memory
        .store
        .write(&mount.scope, document, &content, &memory.actor, read)?;
    Ok(edited(shown, version))
}

pub fn delete(memory: &Memory, path: &str) -> Result<Outcome, Error> {
    let (mount, document) = document_at(memory, path)?;
    writable(mount)?;
    structural(mount)?;
    let shown = virtual_path(mount, document);
    match memory.store.delete(&mount.scope, document, &memory.actor)? {
        Some(version) => Ok(Outcome::ok(format!("Deleted {shown}")).wrote(shown, version, None)),
        None => Ok(Outcome::fail(format!("No document at {shown}")).hint(INDEX_HINT)),
    }
}

/// Move a document. Within a mount it is the store's rename; across
/// mounts it is read, write, delete, and not atomic: no transaction spans
/// two scopes (C1), so a crash between the write and the delete leaves
/// the document in both.
pub fn rename(memory: &Memory, old: &str, new: &str) -> Result<Outcome, Error> {
    let (from, source) = document_at(memory, old)?;
    let (to, target) = document_at(memory, new)?;
    writable(from)?;
    writable(to)?;
    structural(from)?;
    structural(to)?;
    let (store, actor) = (&memory.store, &memory.actor);
    let (verb, version) = if from.path == to.path {
        ("Renamed", store.rename(&from.scope, source, target, actor)?)
    } else {
        let moved = required(memory, from, source)?;
        if store.read(&to.scope, target)?.is_some() {
            let taken = MemoryError::conflict(&to.scope, Some(target), "destination_exists");
            return Err(taken.into());
        }
        let version = store.write(&to.scope, target, &moved.content, actor, None)?;
        store.delete(&from.scope, source, actor)?;
        ("Moved", version)
    };
    let (was, now) = (virtual_path(from, source), virtual_path(to, target));
    let prose = format!("{verb} {was} to {now} (v{version})");
    Ok(Outcome::ok(prose).wrote(now, version, Some(was)))
}

#[cfg(test)]
mod tests {
    use super::match_lines;

    #[test]
    fn every_occurrence_is_found_on_its_line_overlaps_included() {
        assert_eq!(match_lines("a\nb a\n\na", "a"), [1, 2, 4]);
        assert_eq!(match_lines("aaa", "aa"), [1, 1]);
        assert_eq!(match_lines("é\néé", "é"), [1, 2, 2]);
        assert_eq!(match_lines("x\ny", "x\ny"), [1]);
        assert!(match_lines("abc", "d").is_empty());
    }
}
