//! Memory mutations persist a redo plan before their journals, documents and
//! change events. Recovery completes interrupted writes before subsequent reads.
//! Versions remain per document, monotonic from 1; deletes consume a version.

use super::envelope::{Envelope, render};
use super::error::{Error, MemoryError};
use super::grammar::{Actor, validate_document_path};
use super::journal::{self, Action, Row};
use super::layout::{self, DOCUMENTS, VERSIONS};
use super::scalar::spell_str;
use super::time::Stamp;
use super::transaction::WriteOp;
use super::{Document, FileStore};
use crate::changes::model::{Change, Kind};

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

/// The actor as the envelope spells it: plain for every actor of the §20
/// grammar, quoted as PyYAML quotes it for the wire's opaque tail.
fn spelled(actor: &Actor) -> Option<String> {
    Some(spell_str(actor.as_str()))
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
        Ok(self
            .write_document(scope, path, content, actor, expected)?
            .version)
    }

    /// `write`, answering the envelope it wrote (the wire's document).
    pub fn write_document(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<Envelope, Error> {
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
            let conflict =
                MemoryError::conflict_versions(scope, Some(path), reason, expected, found);
            return Err(conflict.into());
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
        let sidecar_write = WriteOp::append(
            &self.root,
            &sidecar,
            journal::render_row(&row(path, version, action, content, actor, now)),
        )?;
        let envelope = Envelope {
            version,
            created_at,
            updated_at: now,
            actor: spelled(actor),
            redacted: false,
            extra,
        };
        self.commit_changes(
            scope,
            vec![
                sidecar_write,
                WriteOp::put(&self.root, &file, render(&envelope, content))?,
            ],
            Change::new(
                if action == Action::Created {
                    Kind::Created
                } else {
                    Kind::Modified
                },
                Some(path),
                Some(version),
            ),
        )?;
        Ok(envelope)
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
        self.commit_changes(
            scope,
            vec![
                WriteOp::append(&self.root, &sidecar, journal::render_row(&gone))?,
                WriteOp::remove(&self.root, &file)?,
            ],
            Change::new(Kind::Deleted, Some(path), Some(version)),
        )?;
        Ok(Some(version))
    }

    /// Move a document within a scope: `deleted` at the source, `created`
    /// at the destination, which continues its own history; the version
    /// written there. A missing source is `memory_document_not_found`, an
    /// occupied destination (the source itself included) a
    /// `memory_conflict`, never a silent overwrite.
    pub fn rename(&self, scope: &str, src: &str, dst: &str, actor: &Actor) -> Result<u64, Error> {
        Ok(self
            .rename_document(scope, src, dst, actor)?
            .envelope
            .version)
    }

    /// `rename`, answering the document at its destination.
    pub fn rename_document(
        &self,
        scope: &str,
        src: &str,
        dst: &str,
        actor: &Actor,
    ) -> Result<Document, Error> {
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
        let source_version = after(&src_rows, &existing);
        let source_write = WriteOp::append(
            &self.root,
            &src_sidecar,
            journal::render_row(&moved(src, source_version, Action::Deleted)),
        )?;
        let dest_write = WriteOp::append(
            &self.root,
            &dst_sidecar,
            journal::render_row(&moved(dst, version, Action::Created)),
        )?;
        let envelope = Envelope {
            version,
            created_at: now,
            updated_at: now,
            actor: spelled(actor),
            ..existing.envelope
        };
        let mut change = Change::new(Kind::Renamed, Some(dst), Some(version));
        change.previous_path = Some(src.into());
        change.previous_version = Some(source_version);
        self.commit_changes(
            scope,
            vec![
                source_write,
                dest_write,
                WriteOp::put(&self.root, &dst_file, render(&envelope, &existing.content))?,
                WriteOp::remove(&self.root, &src_file)?,
            ],
            change,
        )?;
        Ok(Document {
            envelope,
            content: existing.content,
        })
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
        let trail = layout::trail_file(&self.root, scope)?;
        if !targets.is_empty() {
            journal::read_acts(&trail, scope)?;
        }
        let mut prepared = Vec::new();
        for target in &targets {
            let sidecar = layout::journal_file(&self.root, scope, target)?;
            let mut rows = journal::read_rows(&sidecar, scope, target)?;
            let file = layout::doc_file(&self.root, scope, target)?;
            let document = self.present(scope, target, &file)?;
            for row in &mut rows {
                (row.content, row.redacted) = (String::new(), true);
            }
            prepared.push((sidecar, rows, file, document));
        }
        let mut writes = vec![];
        for (sidecar, rows, file, document) in prepared {
            if !rows.is_empty() {
                writes.push(WriteOp::put(
                    &self.root,
                    &sidecar,
                    rows.iter().map(journal::render_row).collect(),
                )?);
            }
            if let Some(document) = document {
                let envelope = Envelope {
                    redacted: true,
                    ..document.envelope
                };
                writes.push(WriteOp::put(&self.root, &file, render(&envelope, ""))?);
            }
        }
        if !targets.is_empty() {
            writes.push(WriteOp::append(
                &self.root,
                &trail,
                journal::render_act(&journal::Act {
                    path: path.map(str::to_string),
                    actor: Some(actor.as_str().into()),
                    created_at: Stamp::now(),
                    count: targets.len() as u64,
                }),
            )?);
            self.commit_changes(
                scope,
                writes,
                Change::new(
                    if path.is_some() {
                        Kind::Erased
                    } else {
                        Kind::ScopeErased
                    },
                    path,
                    None,
                ),
            )?;
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
