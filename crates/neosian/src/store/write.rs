//! The store's mutations (`memory/file.py`, DESIGN §8). Each appends
//! exactly one version row per path it touches, the row before the
//! document: the sidecar is the counter's source of truth, synced before
//! the document can claim its number, so a crash between the two never
//! lets a number be reused. A version is per document, monotonic from 1,
//! and a delete consumes one.

use std::fs;

use super::envelope::{Envelope, render};
use super::error::{Error, MemoryError};
use super::fileio::atomic_write;
use super::grammar::{Actor, validate_document_path};
use super::journal::{self, Action, Row};
use super::layout::{self, DOCUMENTS, REDACTIONS, VERSIONS};
use super::time::Stamp;
use super::{Document, FileStore};

/// The row a mutation appends, by `by` at `at`; `content` is what it records.
fn row(path: &str, version: u64, action: Action, content: &str, by: &Actor, at: Stamp) -> Row {
    Row {
        path: path.to_string(),
        version,
        action,
        content: content.to_string(),
        actor: Some(by.as_str().to_string()),
        created_at: at,
        redacted: false,
    }
}

/// The number a mutation of a live document takes: past the sidecar, and
/// past the version the document declares (a hand-planted one may have no
/// sidecar at all).
fn after(rows: &[Row], existing: &Document) -> u64 {
    journal::next_version(rows).max(existing.envelope.version + 1)
}

impl FileStore {
    /// Create or overwrite; the version written. `expected` is the version
    /// the caller read: another, or no document at all, is a
    /// `memory_conflict`. An identical content still bumps.
    pub fn write(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<u64, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        validate_document_path(path)?;
        let file = layout::doc_file(&self.root, scope, path)?;
        let existing = self.present(scope, path, &file)?;
        let found = existing.as_ref().map(|document| document.envelope.version);
        let lost = match (expected, found) {
            (Some(_), None) => Some("document_absent"),
            (Some(expected), Some(found)) if found != expected => Some("version_mismatch"),
            _ => None,
        };
        if let Some(reason) = lost {
            return Err(MemoryError::conflict(scope, Some(path), reason).into());
        }
        let (sidecar, rows) = self.rows(scope, path)?;
        let now = Stamp::now();
        let (version, action, created_at, extra) = match existing {
            Some(document) => {
                let version = after(&rows, &document);
                let Envelope {
                    created_at, extra, ..
                } = document.envelope;
                (version, Action::Modified, created_at, extra)
            }
            None => (
                journal::next_version(&rows),
                Action::Created,
                now,
                Vec::new(),
            ),
        };
        journal::append_row(&sidecar, &row(path, version, action, content, actor, now))?;
        let envelope = Envelope {
            version,
            created_at,
            updated_at: now,
            actor: Some(actor.as_str().to_string()),
            redacted: false,
            extra,
        };
        atomic_write(&file, &render(&envelope, content), false)?;
        Ok(version)
    }

    /// Remove a live document; the version the deletion consumed, None
    /// when there was nothing to remove.
    pub fn delete(&self, scope: &str, path: &str, actor: &Actor) -> Result<Option<u64>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        validate_document_path(path)?;
        let file = layout::doc_file(&self.root, scope, path)?;
        let Some(existing) = self.present(scope, path, &file)? else {
            return Ok(None);
        };
        let (sidecar, rows) = self.rows(scope, path)?;
        let version = after(&rows, &existing);
        let mut gone = row(
            path,
            version,
            Action::Deleted,
            &existing.content,
            actor,
            Stamp::now(),
        );
        gone.redacted = existing.envelope.redacted;
        journal::append_row(&sidecar, &gone)?;
        fs::remove_file(&file)?;
        Ok(Some(version))
    }

    /// Move a document within a scope: `deleted` at the source, `created`
    /// at the destination, which continues its own history; the version
    /// written there. A missing source is `memory_document_not_found`, an
    /// occupied destination (the source itself included) a
    /// `memory_conflict`, never a silent overwrite.
    pub fn rename(&self, scope: &str, src: &str, dst: &str, actor: &Actor) -> Result<u64, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        validate_document_path(src)?;
        validate_document_path(dst)?;
        let src_file = layout::doc_file(&self.root, scope, src)?;
        if !src_file.is_file() {
            return Err(MemoryError::not_found(scope, src).into());
        }
        let dst_file = layout::doc_file(&self.root, scope, dst)?;
        if dst_file.is_file() {
            return Err(MemoryError::conflict(scope, Some(dst), "destination_exists").into());
        }
        let existing = self.load(scope, src, &src_file)?;
        let now = Stamp::now();
        let (src_sidecar, src_rows) = self.rows(scope, src)?;
        let (dst_sidecar, dst_rows) = self.rows(scope, dst)?;
        let version = journal::next_version(&dst_rows);
        let moved = |path, version, action| {
            let mut moved = row(path, version, action, &existing.content, actor, now);
            moved.redacted = existing.envelope.redacted;
            moved
        };
        journal::append_row(
            &src_sidecar,
            &moved(src, after(&src_rows, &existing), Action::Deleted),
        )?;
        journal::append_row(&dst_sidecar, &moved(dst, version, Action::Created))?;
        let envelope = Envelope {
            version,
            created_at: now,
            updated_at: now,
            actor: Some(actor.as_str().to_string()),
            ..existing.envelope
        };
        atomic_write(&dst_file, &render(&envelope, &existing.content), false)?;
        fs::remove_file(&src_file)?;
        Ok(version)
    }

    /// Clear the content of one path, or of the whole scope, on the live
    /// document and on every version row; the count of distinct paths
    /// matched. The audit skeleton stays (paths, versions, timestamps,
    /// actors), no row is appended and `updated_at` does not move: the act
    /// itself goes on the scope's trail.
    pub fn redact(&self, scope: &str, path: Option<&str>, actor: &Actor) -> Result<usize, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        if let Some(path) = path {
            validate_document_path(path)?;
        }
        let targets = self.redact_targets(scope, path)?;
        for target in &targets {
            let sidecar = layout::journal_file(&self.root, scope, target)?;
            let mut rows = journal::read_rows(&sidecar, scope, target)?;
            if !rows.is_empty() {
                for row in &mut rows {
                    (row.content, row.redacted) = (String::new(), true);
                }
                journal::rewrite_rows(&sidecar, &rows)?;
            }
            let file = layout::doc_file(&self.root, scope, target)?;
            if let Some(document) = self.present(scope, target, &file)? {
                let envelope = Envelope {
                    redacted: true,
                    ..document.envelope
                };
                atomic_write(&file, &render(&envelope, ""), false)?;
            }
        }
        if !targets.is_empty() {
            let trail = layout::scope_dir(&self.root, scope).join(REDACTIONS);
            journal::append_redaction(&trail, path, actor, targets.len())?;
        }
        Ok(targets.len())
    }

    /// The paths a redaction reaches: the one named when it has history or
    /// a document, else every path the scope ever held, sorted.
    fn redact_targets(&self, scope: &str, path: Option<&str>) -> Result<Vec<String>, Error> {
        if let Some(path) = path {
            let known = layout::journal_file(&self.root, scope, path)?.is_file()
                || layout::doc_file(&self.root, scope, path)?.is_file();
            return Ok(Vec::from_iter(known.then(|| path.to_string())));
        }
        let scope_dir = layout::scope_dir(&self.root, scope);
        let mut matched = layout::logical_paths(&scope_dir.join(VERSIONS), ".jsonl")?;
        matched.extend(layout::logical_paths(&scope_dir.join(DOCUMENTS), ".md")?);
        matched.sort();
        matched.dedup();
        Ok(matched)
    }
}

#[cfg(test)]
mod tests;
