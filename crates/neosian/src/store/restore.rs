//! Store mobility's write path (`memory/file_portable.py`, DESIGN §26):
//! the units a store holds, and a verbatim restore of one. Sidecars land
//! first, atomic and synced, so a number can never be reused even if the
//! process dies before the documents land; then the trail, then the
//! documents; everything private. The unit must be empty, else
//! `target_occupied` and nothing is written.

use std::collections::BTreeMap;
use std::path::Path;

use super::FileStore;
use super::envelope::{Envelope, render};
use super::error::{Error, MemoryError};
use super::fileio::{atomic_write, private_mkdir};
use super::grammar::validate_document_path;
use super::journal::{self, Act, Row};
use super::layout::{self, DOCUMENTS, VERSIONS};
use super::ledger::check_scope;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rows::{
    CONVERSATIONS, PROJECTIONS, REDACTIONS as TRAIL, TURNS, render_projection, render_redaction,
    render_turn,
};
use crate::conversation::{self, Projection, Redaction, Refused, Turn};

/// A live document as an archive carries it.
pub struct Restored {
    pub path: String,
    pub content: String,
    pub envelope: Envelope,
}

/// Does a scope directory hold any document, any history or a trail?
fn scope_occupied(scope_dir: &Path) -> std::io::Result<bool> {
    Ok(
        !layout::logical_paths(&scope_dir.join(DOCUMENTS), ".md")?.is_empty()
            || !layout::logical_paths(&scope_dir.join(VERSIONS), ".jsonl")?.is_empty()
            || scope_dir.join(layout::REDACTIONS).is_file(),
    )
}

fn conversation_occupied(directory: &Path) -> bool {
    directory.join(TURNS).is_file() || directory.join(PROJECTIONS).is_file()
}

impl FileStore {
    /// Every conversation holding turns or projections, sorted; a foreign
    /// name is skipped, never ours to read.
    pub fn conversations(&self) -> Result<Vec<String>, Error> {
        let _held = self.lock()?;
        let parent = self.root.join(CONVERSATIONS);
        if !parent.is_dir() {
            return Ok(Vec::new());
        }
        let mut found = Vec::new();
        for entry in std::fs::read_dir(&parent)? {
            let entry = entry?;
            let Ok(name) = entry.file_name().into_string() else {
                continue;
            };
            if parse_conversation_id(&name).is_ok() && conversation_occupied(&entry.path()) {
                found.push(name);
            }
        }
        found.sort();
        Ok(found)
    }

    /// One scope, whole and verbatim: the rows' own numbers, stamps and
    /// actors, the target empty.
    pub fn restore_scope(
        &self,
        scope: &str,
        documents: &[Restored],
        versions: Vec<Row>,
        redactions: &[Act],
    ) -> Result<(), Error> {
        let _held = self.lock()?;
        check_scope(scope)?;
        for path in documents
            .iter()
            .map(|document| document.path.as_str())
            .chain(versions.iter().map(|row| row.path.as_str()))
        {
            validate_document_path(path)?;
        }
        let scope_dir = layout::scope_dir(&self.root, scope);
        if scope_occupied(&scope_dir)? {
            return Err(MemoryError::conflict(scope, None, "target_occupied").into());
        }
        let mut by_path: BTreeMap<String, Vec<Row>> = BTreeMap::new();
        for row in versions {
            by_path.entry(row.path.clone()).or_default().push(row);
        }
        for (path, mut rows) in by_path {
            rows.sort_by_key(|row| row.version);
            journal::rewrite_rows(&layout::journal_file(&self.root, scope, &path)?, &rows)?;
        }
        if !redactions.is_empty() {
            journal::write_acts(&scope_dir.join(layout::REDACTIONS), redactions)?;
        }
        for document in documents {
            let file = layout::doc_file(&self.root, scope, &document.path)?;
            atomic_write(&file, &render(&document.envelope, &document.content), false)?;
        }
        Ok(())
    }

    /// One conversation, whole and verbatim, the target empty.
    pub fn restore_conversation(
        &self,
        conversation_id: &str,
        turns: &[Turn],
        projections: &[Projection],
        redactions: &[Redaction],
    ) -> Result<(), conversation::Error> {
        let _held = self.lock()?;
        parse_conversation_id(conversation_id)?;
        let directory = self.root.join(CONVERSATIONS).join(conversation_id);
        if conversation_occupied(&directory) {
            return Err(Refused::conflict(conversation_id, "target_occupied").into());
        }
        if !turns.is_empty() || !projections.is_empty() {
            private_mkdir(&directory)?;
        }
        if !turns.is_empty() {
            let text: String = turns.iter().map(render_turn).collect();
            atomic_write(&directory.join(TURNS), &text, true)?;
        }
        if !projections.is_empty() {
            let text: String = projections.iter().map(render_projection).collect();
            atomic_write(&directory.join(PROJECTIONS), &text, true)?;
        }
        if !redactions.is_empty() {
            let text: String = redactions.iter().map(render_redaction).collect();
            atomic_write(&directory.join(TRAIL), &text, true)?;
        }
        Ok(())
    }
}
