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
mod commit;
mod commit_check;
mod drafts;
pub(crate) mod lines;
#[cfg_attr(not(test), allow(dead_code))]
mod read;
#[cfg_attr(not(test), allow(dead_code))]
mod restore;
#[cfg_attr(not(test), allow(dead_code))]
mod restore_lines;
#[cfg_attr(not(test), allow(dead_code))]
mod restore_stale;
mod seal;
#[cfg_attr(not(test), allow(dead_code))]
mod search;
#[cfg_attr(not(test), allow(dead_code))]
mod source;
#[cfg_attr(not(test), allow(dead_code))]
mod staging;
#[cfg_attr(not(test), allow(dead_code))]
mod stale;
#[cfg(test)]
mod tests;
#[cfg_attr(not(test), allow(dead_code))]
mod write;

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use super::FileStore;
use super::error::MemoryError;
use super::fileio::{atomic_write, within};
use super::transaction::{clean_temps, sync_parents};
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
    /// The enable marker's path, held inside the root (finding 7).
    fn marker(&self) -> Result<PathBuf, Error> {
        let path = self.root.join(MARKER);
        if within(&self.root, &path)? {
            Ok(path)
        } else {
            let why = "the records marker .records/format escapes the store root";
            Err(MemoryError::configuration(why).into())
        }
    }

    /// A conversation's drafts directory, held inside the root.
    pub(super) fn drafts_dir(&self, conversation_id: &str) -> Result<PathBuf, Error> {
        self.conversation_file(conversation_id, DRAFTS)
    }

    /// A sealed turn's records file, held inside the root. The conversation
    /// directory is resolved once; below it only a symlink could lead
    /// outside, so the full check runs only when `records/` or the file is
    /// one (every read of a records turn passes here).
    pub(super) fn record_file(&self, conversation_id: &str, turn: u64) -> Result<PathBuf, Error> {
        let records = self.conversation_dir(conversation_id)?.join(RECORD_FILES);
        let file = records.join(format!("{turn}.jsonl"));
        let linked = |path: &Path| {
            fs::symlink_metadata(path).is_ok_and(|meta| meta.file_type().is_symlink())
        };
        if (linked(&records) || linked(&file)) && !within(&self.root, &file)? {
            return Err(Refused::id_invalid(conversation_id, "escapes the store root").into());
        }
        Ok(file)
    }

    /// Remove the temp files a killed atomic write left in `directory`, as
    /// the memory journal does (finding 8); the root lock is held.
    fn sweep(&self, directory: &Path) -> Result<(), Error> {
        clean_temps(directory)?;
        Ok(())
    }

    /// Make the entries of `directory` and its parents durable up to the
    /// root, as the memory journal does after a rename or a removal.
    fn sync(&self, file: &Path) -> Result<(), Error> {
        sync_parents(&self.root, file)?;
        Ok(())
    }

    /// `sync` from a directory, or from its nearest parent that is left.
    #[cfg_attr(not(test), allow(dead_code))]
    fn sync_existing(&self, directory: &Path) -> Result<(), Error> {
        let mut at = directory;
        while !at.is_dir() {
            match at.parent() {
                Some(parent) if parent.starts_with(&self.root) => at = parent,
                _ => return Ok(()),
            }
        }
        self.sync(&at.join("entry"))
    }

    /// Whether records are enabled: the marker reads records format 1.
    pub fn records_enabled(&self) -> Result<bool, Error> {
        match fs::read_to_string(self.marker()?) {
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
        let marker = self.marker()?;
        self.sweep(marker.parent().expect("the marker has a directory"))?;
        if self.records_enabled()? {
            return Ok(false);
        }
        atomic_write(&marker, &format!("{MARKER_TEXT}\n"), true)?;
        self.sync(&marker)?;
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
        let path = self.record_file(conversation_id, header.turn)?;
        let records = drafts::read_records(&path, conversation_id, header.turn)?;
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
        let bytes = match fs::File::open(file) {
            Ok(mut handle) => lines::last_line(&mut handle)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        let Some(bytes) = bytes else {
            return Ok(None);
        };
        let Ok(text) = String::from_utf8(bytes) else {
            return Err(not_utf8(conversation_id, lines::line_count(file)?));
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

    pub(super) fn next_number(&self, file: &Path, conversation_id: &str) -> Result<u64, Error> {
        self.last_number(file, conversation_id)?
            .checked_add(1)
            .ok_or_else(|| {
                Refused::format_unsupported(
                    conversation_id,
                    "the log has reached the last turn a conversation can hold",
                )
                .into()
            })
    }

    /// The first row's stamp, the first line alone parsed.
    pub(super) fn first_stamp(
        &self,
        file: &Path,
        conversation_id: &str,
    ) -> Result<Option<crate::store::time::Stamp>, Error> {
        let records = self.records_enabled()?;
        let Some(bytes) = lines::first_line(file)? else {
            return Ok(None);
        };
        let text = String::from_utf8(bytes).map_err(|_| not_utf8(conversation_id, 1))?;
        Ok(Some(match parse_row(&text, 1, conversation_id, records)? {
            Row::Whole(turn) => turn.created_at,
            Row::Records(RecordHeader { created_at, .. }) => created_at,
        }))
    }
}

/// A turn-log line that is not UTF-8, refused by its number (§40.54, the
/// review's finding 11): numbering reads the last line alone, so a bad byte
/// elsewhere no longer refuses an append as the whole-log read did.
fn not_utf8(conversation_id: &str, number: usize) -> Error {
    Refused::format_unsupported(
        conversation_id,
        &format!("turn-log line {number} is not UTF-8"),
    )
    .into()
}
