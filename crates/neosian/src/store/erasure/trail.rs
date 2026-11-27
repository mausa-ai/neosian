//! One conversation's erasure trail in the order its acts were stored, a
//! page at a time, for a transfer (DESIGN §40.54 item 22). The audit read
//! `turn_redactions` answers newest first by time, at most a limit, and
//! stays as it is. A page starts at the byte position the page before it
//! answered, which must still be the start of a row. Each page takes the
//! store lock, runs the store's recovery and reads only its own rows, so
//! the whole trail is never read to make one page.

use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};

use super::super::FileStore;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rows::{REDACTIONS, parse_redaction};
use crate::conversation::search::line_text;
use crate::conversation::{Error, Redaction};

/// Where a trail page starts: the byte offset of a row, and the number of
/// rows before it, so a malformed row is named by its line.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TrailAt {
    pub offset: u64,
    pub rows: u64,
}

impl FileStore {
    /// At most `limit` acts of one conversation's trail from `at`, in the
    /// order they were stored, and where the next page starts while rows
    /// follow this page. A position that is no longer the start of a row
    /// means the trail was rewritten between pages, and the read refuses.
    pub fn trail_page(
        &self,
        conversation_id: &str,
        at: TrailAt,
        limit: usize,
    ) -> Result<(Vec<Redaction>, Option<TrailAt>), Error> {
        let _held = self.lock()?;
        parse_conversation_id(conversation_id)?;
        if limit < 1 {
            return Err(Error::Value(format!("limit must be >= 1, got {limit}")));
        }
        self.settle(conversation_id)?;
        let trail = self.conversation_file(conversation_id, REDACTIONS)?;
        if !trail.is_file() {
            if at == TrailAt::default() {
                return Ok((Vec::new(), None));
            }
            return Err(changed(conversation_id));
        }
        let mut reader = BufReader::new(File::open(&trail)?);
        if !row_start(&mut reader, at.offset)? {
            return Err(changed(conversation_id));
        }
        let (mut acts, mut next) = (Vec::new(), at);
        let mut buffer = String::new();
        while acts.len() < limit {
            buffer.clear();
            let read = reader.read_line(&mut buffer)? as u64;
            if read == 0 {
                return Ok((acts, None));
            }
            next = TrailAt {
                offset: next.offset + read,
                rows: next.rows + 1,
            };
            let row = parse_redaction(line_text(&buffer), next.rows as usize, conversation_id)?;
            acts.push(row);
        }
        // Rows follow only after a line ending, so a later page always
        // starts at the start of a row.
        let more = !reader.fill_buf()?.is_empty();
        Ok((acts, more.then_some(next)))
    }
}

/// Whether `offset` starts a row: the file's start, or just past a line
/// ending within the file. The reader is left at `offset`.
fn row_start(reader: &mut BufReader<File>, offset: u64) -> io::Result<bool> {
    if offset == 0 {
        return Ok(true);
    }
    if offset > reader.get_ref().metadata()?.len() {
        return Ok(false);
    }
    reader.seek(SeekFrom::Start(offset - 1))?;
    let mut before = [0_u8];
    reader.read_exact(&mut before)?;
    Ok(before[0] == b'\n')
}

fn changed(conversation_id: &str) -> Error {
    Error::Value(format!(
        "the erasure trail of {conversation_id} changed while it was read; run the command again"
    ))
}

#[cfg(test)]
#[path = "trail_tests.rs"]
mod tests;
