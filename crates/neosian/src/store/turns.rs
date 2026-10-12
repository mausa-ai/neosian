//! Turn persistence over the root (`conversation/file_turns.py`, DESIGN
//! §9.8), the read side: `root/conversations/<id>/turns.jsonl` and
//! `projections.jsonl`, each read whole under the root-wide lock the
//! memory side holds too, a malformed or newer row refused and never
//! skipped. The appends land with the record verb.

use std::fs;
use std::path::{Path, PathBuf};

use super::FileStore;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rows::{CONVERSATIONS, PROJECTIONS, TURNS, parse_projection, parse_turn};
use crate::conversation::search::search_files;
use crate::conversation::{Error, Projection, Refused, Turn};

impl FileStore {
    /// Every turn of a conversation, oldest first; none for one never
    /// written.
    pub fn read_turns(&self, conversation_id: &str) -> Result<Vec<Turn>, Error> {
        let _held = self.lock()?;
        let file = self.conversation_dir(conversation_id)?.join(TURNS);
        rows(&file, |line, number| {
            parse_turn(line, number, conversation_id)
        })
    }

    /// Every projection entry of a conversation in store order: by turn,
    /// then span, insertion order breaking ties.
    pub fn read_projections(&self, conversation_id: &str) -> Result<Vec<Projection>, Error> {
        let _held = self.lock()?;
        let file = self.conversation_dir(conversation_id)?.join(PROJECTIONS);
        let mut entries = rows(&file, |line, number| {
            parse_projection(line, number, conversation_id)
        })?;
        entries.sort_by_key(|entry| (entry.turn, entry.span));
        Ok(entries)
    }

    /// The turns holding every term, newest first, at most `limit`;
    /// `ids` narrows to those conversations, and an empty list answers
    /// nothing.
    pub fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, Error> {
        let _held = self.lock()?;
        if let Some(ids) = ids {
            for conversation_id in ids {
                parse_conversation_id(conversation_id)?;
            }
            if ids.is_empty() {
                return Ok(Vec::new());
            }
        }
        search_files(&self.root.join(CONVERSATIONS), ids, terms, limit)
    }

    /// The conversation's directory, refused when a symlink would carry
    /// it outside the root.
    fn conversation_dir(&self, conversation_id: &str) -> Result<PathBuf, Error> {
        parse_conversation_id(conversation_id)?;
        let candidate = self.root.join(CONVERSATIONS).join(conversation_id);
        let mut existing = candidate.as_path();
        while !existing.exists() {
            existing = existing.parent().unwrap_or(&self.root);
        }
        let below = candidate.strip_prefix(existing).unwrap_or(&candidate);
        if existing.canonicalize()?.join(below).starts_with(&self.root) {
            Ok(candidate)
        } else {
            Err(Refused::id_invalid(conversation_id, "escapes the store root").into())
        }
    }
}

/// Every row of a JSONL file through `parse`, numbered from 1; a missing
/// file is no rows.
fn rows<T>(
    file: &Path,
    parse: impl Fn(&str, usize) -> Result<T, Refused>,
) -> Result<Vec<T>, Error> {
    if !file.is_file() {
        return Ok(Vec::new());
    }
    let text = fs::read_to_string(file)?;
    text.lines()
        .enumerate()
        .map(|(index, line)| parse(line, index + 1).map_err(Error::from))
        .collect()
}
