//! Turn persistence over the root (`conversation/file_turns.py`, DESIGN
//! §9.8), the read side: `root/conversations/<id>/turns.jsonl` and
//! `projections.jsonl`, each read whole under the root-wide lock the
//! memory side holds too, a malformed or newer row refused and never
//! skipped; the turn append the record verb lands, unsynced as the
//! Python side writes it.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use super::FileStore;
use super::fileio::append_line;
use super::time::Stamp;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::message::Message;
use crate::conversation::rows::{
    CONVERSATIONS, PROJECTIONS, TURNS, parse_projection, parse_turn, render_turn,
};
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

    /// One turn appended under the root lock: the number after the log's
    /// last row, the row written whole in one unsynced append.
    pub fn append_turn(
        &self,
        conversation_id: &str,
        messages: Vec<Message>,
        actor: Option<String>,
    ) -> Result<Turn, Error> {
        let _held = self.lock()?;
        if messages.is_empty() {
            return Err(io::Error::other("a turn must carry at least one message").into());
        }
        let file = self.conversation_dir(conversation_id)?.join(TURNS);
        let turn = Turn {
            conversation_id: conversation_id.to_string(),
            turn: last_number(&file, conversation_id)? + 1,
            messages,
            created_at: Stamp::now(),
            actor,
            redacted: false,
        };
        append_line(&file, &render_turn(&turn), false)?;
        Ok(turn)
    }

    /// When a conversation began: its first row's stamp, that row alone
    /// parsed; None for one never written.
    pub fn first_turn_stamp(&self, conversation_id: &str) -> Result<Option<Stamp>, Error> {
        let _held = self.lock()?;
        let file = self.conversation_dir(conversation_id)?.join(TURNS);
        if !file.is_file() {
            return Ok(None);
        }
        let text = fs::read_to_string(&file)?;
        match text.lines().next() {
            Some(line) => Ok(Some(parse_turn(line, 1, conversation_id)?.created_at)),
            None => Ok(None),
        }
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

/// The number of a log's last row, 0 for no log; the last line alone is
/// parsed, and refused as any row would be.
fn last_number(file: &Path, conversation_id: &str) -> Result<u64, Error> {
    if !file.is_file() {
        return Ok(0);
    }
    let text = fs::read_to_string(file)?;
    let count = text.lines().count();
    match text.lines().last() {
        None => Ok(0),
        Some(line) => Ok(parse_turn(line, count, conversation_id)?.turn),
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
