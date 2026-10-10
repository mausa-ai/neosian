//! The JSONL version sidecar (`memory/journal.py`, C5): one row per
//! mutation with the full content. It outlives the document across a
//! delete, which is what makes a re-created document continue its
//! numbering, so a malformed line raises and is never skipped: skipping
//! would let a version number be reused.

use std::fs;
use std::io;
use std::path::Path;

use serde_json::{Value, json};

use super::FORMAT;
use super::error::{Error, MemoryError};
use super::fileio::{append_line, atomic_write};
use super::grammar::Actor;
use super::time::{Stamp, Unreadable};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Created,
    Modified,
    Deleted,
}

impl Action {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Modified => "modified",
            Self::Deleted => "deleted",
        }
    }

    fn parse(text: &str) -> Option<Self> {
        [Self::Created, Self::Modified, Self::Deleted]
            .into_iter()
            .find(|action| action.as_str() == text)
    }
}

/// One audit row: a mutation of one document. After a redaction the
/// skeleton survives, the content empty and `redacted` set.
#[derive(Debug)]
pub struct Row {
    pub path: String,
    pub version: u64,
    pub action: Action,
    pub content: String,
    pub actor: Option<String>,
    pub created_at: Stamp,
    pub redacted: bool,
}

/// Every row, oldest first; a missing sidecar is an empty history.
pub fn read_rows(file: &Path, scope: &str, path: &str) -> Result<Vec<Row>, Error> {
    if !file.is_file() {
        return Ok(Vec::new());
    }
    let rows = fs::read_to_string(file)?
        .lines()
        .enumerate()
        .map(|(index, line)| parse_row(line, index + 1, scope, path))
        .collect::<Result<_, _>>()?;
    Ok(rows)
}

/// The next monotonic version number of a document path.
pub fn next_version(rows: &[Row]) -> u64 {
    rows.last().map_or(1, |row| row.version + 1)
}

/// Append one complete row in a single write, synced: the sidecar is
/// written before the document, and the row must be on disk before the
/// document can claim its number, or a crash between the two could let
/// the number be reused.
pub fn append_row(file: &Path, row: &Row) -> io::Result<()> {
    append_line(file, &render_row(row), true)
}

/// Replace the whole sidecar atomically (a redaction rewrites every row).
pub fn rewrite_rows(file: &Path, rows: &[Row]) -> io::Result<()> {
    atomic_write(file, &rows.iter().map(render_row).collect::<String>(), true)
}

/// One erasure act on a scope's trail: who cleared what, when, and how
/// many distinct paths; `path` is None for the whole scope.
pub fn append_redaction(
    trail: &Path,
    path: Option<&str>,
    actor: &Actor,
    count: usize,
) -> io::Result<()> {
    let act = json!({
        "ts": Stamp::now().z(),
        "actor": actor.as_str(),
        "path": path,
        "count": count,
    });
    append_line(trail, &format!("{act}\n"), false)
}

/// A row as one JSON line. Parity's unit for a row is the parsed object
/// (ledger #348): key order and escaping are the writer's own.
fn render_row(row: &Row) -> String {
    let row = json!({
        "neosian_format": FORMAT,
        "path": row.path,
        "version": row.version,
        "action": row.action.as_str(),
        "content": row.content,
        "actor": row.actor,
        "created_at": row.created_at.z(),
        "redacted": row.redacted,
    });
    format!("{row}\n")
}

fn parse_row(line: &str, number: usize, scope: &str, path: &str) -> Result<Row, MemoryError> {
    let place = format!("sidecar line {number}");
    let refuse = |reason: String| MemoryError::format_unsupported(scope, path, &reason);
    let data: Value =
        serde_json::from_str(line).map_err(|_| refuse(format!("malformed {place}")))?;
    let Some(data) = data.as_object() else {
        return Err(refuse(format!("{place} is not an object")));
    };
    let declared = data.get("neosian_format").and_then(Value::as_u64);
    let Some(declared) = declared.filter(|declared| *declared >= 1) else {
        return Err(refuse(format!("{place} missing format")));
    };
    if declared > FORMAT {
        return Err(refuse(format!(
            "{place} format {declared} is newer than supported"
        )));
    }
    let text = |key: &str| data.get(key).and_then(Value::as_str);
    let fields = || {
        let actor = match data.get("actor") {
            None | Some(Value::Null) => None,
            Some(actor) => Some(actor.as_str()?.to_string()),
        };
        let redacted = data.get("redacted").map_or(Some(false), Value::as_bool)?;
        let version = data
            .get("version")?
            .as_u64()
            .filter(|version| *version >= 1)?;
        let action = Action::parse(text("action")?)?;
        Some((
            version,
            action,
            text("content")?,
            actor,
            redacted,
            text("created_at")?,
            text("path")?,
        ))
    };
    let Some((version, action, content, actor, redacted, created_at, row_path)) = fields() else {
        return Err(refuse(format!("{place} has invalid fields")));
    };
    let created_at = Stamp::parse(created_at).map_err(|error| {
        let kind = match error {
            Unreadable::Malformed => "an unreadable",
            Unreadable::Naive => "a naive",
        };
        refuse(format!("{place} has {kind} timestamp"))
    })?;
    Ok(Row {
        path: row_path.to_string(),
        version,
        action,
        content: content.to_string(),
        actor,
        created_at,
        redacted,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(line: &str) -> Result<Row, String> {
        parse_row(line, 4, "user:test", "facts").map_err(|error| {
            let prefix = "Unsupported memory format for 'facts' in scope 'user:test': ";
            error
                .message
                .strip_prefix(prefix)
                .unwrap_or(&error.message)
                .to_string()
        })
    }

    #[test]
    fn a_row_reads_as_the_python_side_writes_it() {
        let read = row(r##"{"neosian_format":1,"path":"facts","version":2,"action":"modified","content":"# Facts\n\n- café\n","actor":"seed","created_at":"2026-10-10T18:20:18.276022Z","redacted":false}"##).unwrap();
        assert_eq!(
            (read.path.as_str(), read.version, read.action),
            ("facts", 2, Action::Modified)
        );
        assert_eq!(read.content, "# Facts\n\n- café\n");
        assert_eq!(read.actor.as_deref(), Some("seed"));
        assert_eq!(read.created_at.iso(), "2026-10-10T18:20:18.276022+00:00");
        assert!(!read.redacted);
        let bare = row(r#"{"neosian_format":1,"path":"p","version":1,"action":"deleted","content":"","actor":null,"created_at":"2026-10-10T18:20:18Z"}"#).unwrap();
        assert_eq!(
            (bare.actor, bare.redacted, bare.action),
            (None, false, Action::Deleted)
        );
    }

    #[test]
    fn a_row_written_here_reads_back_whole() {
        let written = Row {
            path: "notes/café".to_string(),
            version: 7,
            action: Action::Deleted,
            content: "# Notes\n\n- \"quoted\" \\ ✓\u{1}\n".to_string(),
            actor: Some("cli:local#3".to_string()),
            created_at: Stamp::parse("2026-10-10T18:20:18.000001Z").unwrap(),
            redacted: true,
        };
        let line = render_row(&written);
        assert!(line.ends_with("}\n") && line.matches('\n').count() == 1);
        assert!(line.contains(r#""created_at":"2026-10-10T18:20:18.000001Z""#));
        let read = parse_row(&line, 1, "user:test", "notes").unwrap();
        assert_eq!(format!("{read:?}"), format!("{written:?}"));
        assert_eq!(next_version(&[]), 1);
        assert_eq!(next_version(&[read]), 8);
    }

    #[test]
    fn a_malformed_row_raises_and_is_never_skipped() {
        let with = |field: &str, value: &str| {
            let good = r#"{"neosian_format":1,"path":"p","version":1,"action":"created","content":"","actor":null,"created_at":"2026-10-10T18:20:18Z","redacted":false}"#;
            let mut data: serde_json::Map<String, Value> = serde_json::from_str(good).unwrap();
            data.insert(field.to_string(), serde_json::from_str(value).unwrap());
            Value::Object(data).to_string()
        };
        for (line, why) in [
            (String::new(), "malformed sidecar line 4"),
            ("{".to_string(), "malformed sidecar line 4"),
            ("[]".to_string(), "sidecar line 4 is not an object"),
            ("{}".to_string(), "sidecar line 4 missing format"),
            (
                with("neosian_format", "true"),
                "sidecar line 4 missing format",
            ),
            (
                with("neosian_format", "2"),
                "sidecar line 4 format 2 is newer than supported",
            ),
            (with("version", "0"), "sidecar line 4 has invalid fields"),
            (with("version", "1.0"), "sidecar line 4 has invalid fields"),
            (
                with("action", "\"redacted\""),
                "sidecar line 4 has invalid fields",
            ),
            (with("actor", "7"), "sidecar line 4 has invalid fields"),
            (
                with("redacted", "null"),
                "sidecar line 4 has invalid fields",
            ),
            (
                with("created_at", "\"soon\""),
                "sidecar line 4 has an unreadable timestamp",
            ),
            (
                with("created_at", "\"2026-10-10T18:20:18\""),
                "sidecar line 4 has a naive timestamp",
            ),
        ] {
            assert_eq!(row(&line).unwrap_err(), why, "{line}");
        }
    }
}
