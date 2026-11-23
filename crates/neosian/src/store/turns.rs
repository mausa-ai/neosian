//! Turn persistence over the root (`conversation/file_turns.py`, DESIGN
//! §9.8): `root/conversations/<id>/turns.jsonl` and `projections.jsonl`,
//! each read whole under the root-wide lock the memory side holds too, a
//! malformed or newer row refused and never skipped; the appends the
//! record verb and the wire land, unsynced as the Python side writes them.

use std::fs;
use std::path::{Path, PathBuf};

use super::FileStore;
use super::fileio::{append_line, private_mkdir};
use super::time::Stamp;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::message::Message;
use crate::conversation::rows::{
    CONVERSATIONS, PROJECTIONS, TURNS, parse_projection, render_projection, render_turn,
};
use crate::conversation::search::line_text;
use crate::conversation::{Error, Projection, Refused, Turn};

/// `_check_cursor`: the ABC's own refusals of a negative start or limit.
fn check_cursor(after: i64, limit: Option<i64>) -> Result<(), Error> {
    if after < 0 {
        return Err(Error::Value(format!("after must be >= 0, got {after}")));
    }
    if let Some(limit) = limit.filter(|limit| *limit < 0) {
        return Err(Error::Value(format!("limit must be >= 0, got {limit}")));
    }
    Ok(())
}

/// The rows past `after`, at most `limit`.
fn window<T>(rows: Vec<T>, after: i64, limit: Option<i64>, turn_of: impl Fn(&T) -> u64) -> Vec<T> {
    let mut kept: Vec<T> = rows
        .into_iter()
        .filter(|row| turn_of(row) > after as u64)
        .collect();
    if let Some(limit) = limit {
        kept.truncate(limit as usize);
    }
    kept
}

impl FileStore {
    /// The turns past `after`, oldest first, at most `limit`; none for a
    /// conversation never written.
    pub fn read_turns(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Turn>, Error> {
        let mut turns = Vec::new();
        self.read_turns_each(conversation_id, after, limit, &mut |turn| {
            turns.push(turn);
            Ok(true)
        })?;
        Ok(turns)
    }

    /// `read_turns` one turn at a time (DESIGN §40.46): the log streams a
    /// line at a time under the lock and every row is still decoded, so a
    /// malformed row anywhere refuses as a whole read would, even after
    /// `visit` stopped taking turns.
    pub fn read_turns_each(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let _held = self.lock()?;
        let rows = self.turn_reader(conversation_id)?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        check_cursor(after, limit)?;
        if !file.is_file() {
            return Ok(());
        }
        let mut reader = std::io::BufReader::new(fs::File::open(&file)?);
        let (mut buffer, mut number, mut taken) = (String::new(), 0_usize, 0_i64);
        let mut open = limit != Some(0);
        loop {
            buffer.clear();
            if std::io::BufRead::read_line(&mut reader, &mut buffer)? == 0 {
                return Ok(());
            }
            number += 1;
            let row = rows.row(line_text(&buffer), number, conversation_id)?;
            if open && row.turn() > after as u64 {
                taken += 1;
                let turn = self.assemble(&rows, conversation_id, row)?;
                open = visit(turn)? && limit.is_none_or(|limit| taken < limit);
            }
        }
    }

    /// One size page of `read_turns` (DESIGN §40.46): from `hint`, the byte
    /// offset and line number of the row after `after` when an earlier page
    /// found it, else from the log's start, decoding each row it passes; it
    /// stops when `visit` declines a turn and answers where that turn's row
    /// starts. A hint is trusted only at a line start whose row holds turn
    /// `after + 1`; otherwise the page reads from the start. Across the pages
    /// of one read every row is decoded once, so a malformed row still
    /// refuses the read, on the page that reaches it.
    pub fn read_turns_page(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
        hint: Option<(u64, u64)>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<Option<(u64, u64)>, Error> {
        use std::io::{BufRead, Read, Seek, SeekFrom};
        let _held = self.lock()?;
        let rows = self.turn_reader(conversation_id)?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        check_cursor(after, limit)?;
        if !file.is_file() || limit == Some(0) {
            return Ok(None);
        }
        let mut reader = std::io::BufReader::new(fs::File::open(&file)?);
        let (mut offset, mut number) = (0_u64, 0_u64);
        let mut buffer = String::new();
        if let Some((at, line)) = hint.filter(|(at, line)| *at > 0 && *line > 1) {
            reader.seek(SeekFrom::Start(at - 1))?;
            let mut before = [0_u8];
            let boundary = reader.read(&mut before)? == 1 && before[0] == b'\n';
            let next = boundary && reader.read_line(&mut buffer)? > 0 && {
                let row = rows.row(line_text(&buffer), line as usize, conversation_id);
                row.is_ok_and(|row| row.turn() == after as u64 + 1)
            };
            (offset, number) = if next { (at, line - 1) } else { (0, 0) };
            reader.seek(SeekFrom::Start(offset))?;
        }
        let mut taken = 0_i64;
        loop {
            buffer.clear();
            let read = reader.read_line(&mut buffer)? as u64;
            if read == 0 {
                return Ok(None);
            }
            let start = offset;
            (offset, number) = (offset + read, number + 1);
            let row = rows.row(line_text(&buffer), number as usize, conversation_id)?;
            if row.turn() > after as u64 {
                if !visit(self.assemble(&rows, conversation_id, row)?)? {
                    return Ok(Some((start, number)));
                }
                taken += 1;
                if limit.is_some_and(|limit| taken >= limit) {
                    return Ok(None);
                }
            }
        }
    }

    /// The projection entries past `after` in store order (by turn, then
    /// span, insertion order breaking ties), at most `limit`.
    pub fn read_projections(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Projection>, Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, PROJECTIONS)?;
        check_cursor(after, limit)?;
        let mut entries = rows(&file, |line, number| {
            parse_projection(line, number, conversation_id)
        })?;
        entries.sort_by_key(|entry| (entry.turn, entry.span));
        Ok(window(entries, after, limit, |entry| entry.turn))
    }

    /// The last turn's number, 0 for a conversation never written.
    pub fn last_turn_number(&self, conversation_id: &str) -> Result<u64, Error> {
        let _held = self.lock()?;
        self.settle(conversation_id)?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        self.last_number(&file, conversation_id)
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
        let file = self.conversation_file(conversation_id, TURNS)?;
        if messages.is_empty() {
            return Err(Error::Value(
                "a turn must carry at least one message".to_string(),
            ));
        }
        self.settle(conversation_id)?;
        let turn = Turn {
            conversation_id: conversation_id.to_string(),
            turn: self.next_number(&file, conversation_id)?,
            messages,
            created_at: Stamp::now(),
            actor,
            redacted: false,
        };
        append_line(&file, &render_turn(&turn), false)?;
        Ok(turn)
    }

    /// Projection entries appended in one unsynced write; none is nothing.
    pub fn append_projections(
        &self,
        conversation_id: &str,
        entries: &[Projection],
    ) -> Result<(), Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, PROJECTIONS)?;
        if entries.is_empty() {
            return Ok(());
        }
        private_mkdir(file.parent().unwrap_or(&self.root))?;
        let text: String = entries.iter().map(render_projection).collect();
        append_line(&file, &text, false)?;
        Ok(())
    }

    /// When a conversation began: its first row's stamp, that line alone
    /// read and parsed; None for one never written.
    pub fn first_turn_stamp(&self, conversation_id: &str) -> Result<Option<Stamp>, Error> {
        let _held = self.lock()?;
        self.settle(conversation_id)?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        self.first_stamp(&file, conversation_id)
    }

    /// The conversation's directory, refused when a symlink would carry
    /// it outside the root.
    pub(super) fn conversation_dir(&self, conversation_id: &str) -> Result<PathBuf, Error> {
        parse_conversation_id(conversation_id)?;
        let candidate = self.root.join(CONVERSATIONS).join(conversation_id);
        if super::fileio::within(&self.root, &candidate)? {
            Ok(candidate)
        } else {
            Err(Refused::id_invalid(conversation_id, "escapes the store root").into())
        }
    }

    pub(super) fn conversation_file(&self, id: &str, name: &str) -> Result<PathBuf, Error> {
        let file = self.conversation_dir(id)?.join(name);
        if !super::fileio::within(&self.root, &file)? {
            return Err(Refused::id_invalid(id, "escapes the store root").into());
        }
        Ok(file)
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
