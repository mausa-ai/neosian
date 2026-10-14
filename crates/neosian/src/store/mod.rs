//! `FileStore`: the file substrate (`memory/file.py`, DESIGN §8, §39.3),
//! written a second time so the binary reads and writes the home the
//! Python side does. One directory per scope segment:
//!
//! ```text
//! root/user%3A123/proj%3Aerp/
//!     documents/notes/api.md      envelope frontmatter + verbatim body
//!     versions/notes/api.jsonl    one row per mutation, full content
//!     redactions.jsonl            {ts, actor, path|null, count} per redact
//! ```
//!
//! Every public call holds the root-wide advisory lock for its whole
//! length, reads included, so the hooks, MCP servers and shells of both
//! implementations may share one root.

pub mod envelope;
pub mod error;
pub(crate) mod fileio;
pub mod grammar;
pub mod journal;
pub mod layout;
pub(crate) mod lock;
pub mod time;
mod turns;
mod write;

use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};

use self::envelope::Envelope;
use self::error::{Error, MemoryError};
use self::grammar::{parse_scope, validate_document_path};
use self::journal::Row;
use self::layout::{DOCUMENTS, REDACTIONS, VERSIONS};
use self::time::Stamp;

/// `neosian_format`: the storage format this build reads; anything newer
/// is refused (C6).
pub const FORMAT: u64 = 1;

/// The current state of one document.
pub struct Document {
    pub envelope: Envelope,
    pub content: String,
}

/// One row of a scope listing: a deleted document never appears, a
/// redacted one appears flagged.
pub struct Entry {
    pub path: String,
    pub updated_at: Stamp,
    pub redacted: bool,
}

pub struct FileStore {
    root: PathBuf,
}

impl FileStore {
    /// The store at `root`, created private when absent.
    pub fn open(root: &Path) -> io::Result<Self> {
        let root = std::path::absolute(root)?;
        fileio::private_mkdir(&root)?;
        Ok(Self {
            root: root.canonicalize()?,
        })
    }

    /// None for a path never written or since deleted; a redacted
    /// document reads back with empty content.
    pub fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        validate_document_path(path)?;
        self.present(scope, path, &layout::doc_file(&self.root, scope, path)?)
    }

    /// The live documents of a scope, sorted by path; `prefix` is a plain
    /// string match, never validated.
    pub fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        let documents = layout::scope_dir(&self.root, scope).join(DOCUMENTS);
        let mut entries = Vec::new();
        for path in layout::logical_paths(&documents, ".md")? {
            if !path.starts_with(prefix) {
                continue;
            }
            let Document { envelope, .. } =
                self.load(scope, &path, &documents.join(format!("{path}.md")))?;
            entries.push(Entry {
                path,
                updated_at: envelope.updated_at,
                redacted: envelope.redacted,
            });
        }
        Ok(entries)
    }

    /// A document's version rows, newest first, the most recent `limit`.
    pub fn versions(&self, scope: &str, path: &str, limit: usize) -> Result<Vec<Row>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        validate_document_path(path)?;
        let (_, mut rows) = self.rows(scope, path)?;
        rows.reverse();
        rows.truncate(limit);
        Ok(rows)
    }

    /// Every scope holding documents, history or redactions, sorted
    /// (`file_portable.py`): a directory whose every component carries an
    /// encoded colon and spells a scope of the grammar; a foreign one is
    /// skipped, never ours to read.
    pub fn scopes(&self) -> Result<Vec<String>, Error> {
        let _held = self.lock()?;
        let mut found = Vec::new();
        walk_scopes(&self.root, &[], &mut found)?;
        found.sort();
        Ok(found)
    }

    fn lock(&self) -> io::Result<File> {
        lock::acquire(&self.root.join(".store.lock"))
    }

    /// Validate the scope and refuse a case fold, on every access: another
    /// process may have created the conflicting spelling between calls.
    fn scope(&self, scope: &str) -> Result<(), Error> {
        parse_scope(scope)?;
        if layout::case_collision(&self.root, scope)? {
            return Err(MemoryError::conflict(scope, None, "case_collision").into());
        }
        Ok(())
    }

    /// A path's sidecar and its rows, oldest first. Every row records the
    /// spelling it was written under, so a case-folding filesystem handing
    /// back another path's history is caught here (MC-13).
    fn rows(&self, scope: &str, path: &str) -> Result<(PathBuf, Vec<Row>), Error> {
        let file = layout::journal_file(&self.root, scope, path)?;
        let rows = journal::read_rows(&file, scope, path)?;
        if rows.first().is_some_and(|row| row.path != path) {
            return Err(MemoryError::conflict(scope, Some(path), "case_collision").into());
        }
        Ok((file, rows))
    }

    /// The live document at `file`, None when there is none.
    fn present(&self, scope: &str, path: &str, file: &Path) -> Result<Option<Document>, Error> {
        if !file.is_file() {
            return Ok(None);
        }
        self.load(scope, path, file).map(Some)
    }

    fn load(&self, scope: &str, path: &str, file: &Path) -> Result<Document, Error> {
        let text = fs::read_to_string(file)?;
        let (envelope, content) = envelope::parse(&text, scope, path)?;
        Ok(Document {
            envelope,
            content: content.to_string(),
        })
    }
}

fn walk_scopes(dir: &Path, parts: &[String], found: &mut Vec<String>) -> io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        if !entry.path().is_dir() || !name.contains("%3A") {
            continue;
        }
        let below: Vec<String> = parts.iter().cloned().chain([name]).collect();
        let decoded: Vec<String> = below.iter().map(|part| part.replace("%3A", ":")).collect();
        let scope = decoded.join("/");
        if parse_scope(&scope).is_err() {
            continue;
        }
        if occupied(&entry.path())? {
            found.push(scope);
        }
        walk_scopes(&entry.path(), &below, found)?;
    }
    Ok(())
}

/// Does a scope directory hold any document, any history or a trail?
fn occupied(scope_dir: &Path) -> io::Result<bool> {
    fn holds(dir: &Path, suffix: &str) -> io::Result<bool> {
        if !dir.is_dir() {
            return Ok(false);
        }
        for entry in fs::read_dir(dir)? {
            let path = entry?.path();
            let found = if path.is_dir() {
                holds(&path, suffix)?
            } else {
                path.to_string_lossy().ends_with(suffix)
            };
            if found {
                return Ok(true);
            }
        }
        Ok(false)
    }
    Ok(holds(&scope_dir.join(DOCUMENTS), ".md")?
        || holds(&scope_dir.join(VERSIONS), ".jsonl")?
        || scope_dir.join(REDACTIONS).is_file())
}

#[cfg(test)]
mod tests;
