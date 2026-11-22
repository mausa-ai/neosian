//! Turns stored as records on a FileStore (DESIGN §40.54 items 4, 7 and 9):
//! the enable marker, drafts written in parts, the seal and its
//! roll-forward, the turn log's first and last rows read alone, and a
//! format-2 turn assembled whole for the legacy reads.
//!
//! ```text
//! .records/format                            {"neosian_records":1}
//! conversations/<id>/records/<turn>.jsonl    a sealed turn's records
//! conversations/<id>/.drafts/<draft>.jsonl   an open draft's records
//! conversations/<id>/.drafts/<draft>.state   its state
//! conversations/<id>/.drafts/<draft>.sealed  the header a sealed draft made
//! conversations/<id>/.drafts/sealing         the header of a seal under way
//! ```

// Writing reaches no route or command until the wire (T1d) and the
// recorder (T1e1); the allowance goes with them.
#[cfg_attr(not(test), allow(dead_code))]
mod drafts;
pub(crate) mod lines;
mod seal;
#[cfg(test)]
mod tests;
#[cfg_attr(not(test), allow(dead_code))]
mod write;

use std::fs;
use std::io;
use std::path::Path;

use super::FileStore;
use super::error::MemoryError;
use super::fileio::atomic_write;
use crate::conversation::rows_header::{RecordHeader, Row, parse_row};
use crate::conversation::turn_records::messages_of;
use crate::conversation::{Error, Refused, Turn};
use crate::limits;

pub(super) const DRAFTS: &str = ".drafts";
pub(super) const RECORD_FILES: &str = "records";
const MARKER: &str = ".records/format";
const MARKER_TEXT: &str = "{\"neosian_records\":1}";

/// How one conversation's rows read: whether format 2 is known here, and
/// the largest format-2 turn a legacy read assembles.
pub struct TurnReader {
    records: bool,
    max_turn_bytes: u64,
}

impl TurnReader {
    /// One row, parsed alone: a format-2 row is its header.
    pub fn row(&self, text: &str, number: usize, conversation_id: &str) -> Result<Row, Refused> {
        parse_row(text, number, conversation_id, self.records)
    }
}

impl FileStore {
    /// Whether records are enabled: the marker reads records format 1.
    pub fn records_enabled(&self) -> Result<bool, Error> {
        match fs::read_to_string(self.root.join(MARKER)) {
            Ok(text) if text.trim_end() == MARKER_TEXT => Ok(true),
            Ok(_) => Err(MemoryError::configuration(
                "the records marker .records/format is not records format 1",
            )
            .into()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error.into()),
        }
    }

    /// Enable records (`neosian records enable`); false when they already were.
    pub fn enable_records(&self) -> Result<bool, Error> {
        let _held = self.lock()?;
        if self.records_enabled()? {
            return Ok(false);
        }
        atomic_write(&self.root.join(MARKER), &format!("{MARKER_TEXT}\n"), true)?;
        Ok(true)
    }

    /// Rolls a marked seal forward, then says how the rows read. Every turn
    /// operation on a conversation starts here, under the root lock.
    pub(super) fn turn_reader(&self, conversation_id: &str) -> Result<TurnReader, Error> {
        self.settle(conversation_id)?;
        let records = self.records_enabled()?;
        let max_turn_bytes = if records {
            limits::Records::load()?.max_turn_bytes
        } else {
            0
        };
        Ok(TurnReader {
            records,
            max_turn_bytes,
        })
    }

    /// A row as a whole turn, a format-2 one assembled from its records.
    pub(super) fn assemble(
        &self,
        reader: &TurnReader,
        conversation_id: &str,
        row: Row,
    ) -> Result<Turn, Error> {
        self.whole(conversation_id, row, reader.max_turn_bytes)
    }

    /// A format-2 turn assembled whole, refused past `max_turn_bytes`.
    pub(super) fn whole(
        &self,
        conversation_id: &str,
        row: Row,
        max_turn_bytes: u64,
    ) -> Result<Turn, Error> {
        let header = match row {
            Row::Whole(turn) => return Ok(turn),
            Row::Records(header) => header,
        };
        if header.bytes > max_turn_bytes {
            let why =
                limits::turn_too_long(header.turn, conversation_id, header.bytes, max_turn_bytes);
            return Err(Error::Value(why));
        }
        let path = self
            .conversation_dir(conversation_id)?
            .join(RECORD_FILES)
            .join(format!("{}.jsonl", header.turn));
        let records = drafts::read_records(&path, conversation_id)?;
        let bytes: u64 = records.iter().map(|record| record.text.len() as u64).sum();
        let mismatch = |why: String| {
            Refused::format_unsupported(conversation_id, &format!("turn {}: {why}", header.turn))
        };
        if records.len() as u64 != header.records || bytes != header.bytes {
            return Err(mismatch("its records do not match its header".into()).into());
        }
        let messages = messages_of(&records).map_err(mismatch)?;
        Ok(Turn {
            conversation_id: conversation_id.to_string(),
            turn: header.turn,
            messages,
            created_at: header.created_at,
            actor: header.actor,
            redacted: false,
        })
    }

    /// The log's last row, parsed alone; None for no log or an empty one.
    pub(super) fn last_row(
        &self,
        file: &Path,
        conversation_id: &str,
    ) -> Result<Option<Row>, Error> {
        let records = self.records_enabled()?;
        let text = match fs::File::open(file) {
            Ok(mut handle) => lines::last_line(&mut handle)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        let Some(text) = text else {
            return Ok(None);
        };
        match parse_row(&text, 0, conversation_id, records) {
            Ok(row) => Ok(Some(row)),
            Err(_) => {
                let number = lines::line_count(file)?;
                let refused = parse_row(&text, number, conversation_id, records).err();
                Err(refused.expect("the same row refuses again").into())
            }
        }
    }

    /// The last turn's number, 0 for no turns; one line read.
    pub(super) fn last_number(&self, file: &Path, conversation_id: &str) -> Result<u64, Error> {
        Ok(self
            .last_row(file, conversation_id)?
            .map_or(0, |row| row.turn()))
    }

    /// The first row's stamp, the first line alone parsed.
    pub(super) fn first_stamp(
        &self,
        file: &Path,
        conversation_id: &str,
    ) -> Result<Option<crate::store::time::Stamp>, Error> {
        let records = self.records_enabled()?;
        let Some(text) = lines::first_line(file)? else {
            return Ok(None);
        };
        Ok(Some(match parse_row(&text, 1, conversation_id, records)? {
            Row::Whole(turn) => turn.created_at,
            Row::Records(RecordHeader { created_at, .. }) => created_at,
        }))
    }
}
