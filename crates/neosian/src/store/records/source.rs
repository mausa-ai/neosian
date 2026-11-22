//! What a record read reads from (§40.54 item 10): a page's fill, a turn's
//! records a line at a time from its records file or converted whole, a
//! records file or the turn log entered at a hint only where its line holds
//! what the hint names, and where a fetched piece begins.

use std::fs;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::Path;

use super::TurnReader;
use super::drafts::parse_line;
use crate::conversation::search::line_text;
use crate::conversation::turn_records::{ReadPosition, TurnRecord};
use crate::conversation::{Error, Refused};

pub struct Page {
    pub limit: u64,
    pub used: u64,
    pub count: usize,
}

impl Page {
    pub fn admit(&mut self, bytes: u64) -> bool {
        if self.count > 0 && self.used + bytes > self.limit {
            return false;
        }
        self.used += bytes;
        self.count += 1;
        true
    }
}

/// A turn's records as the read reaches them.
pub enum Source {
    File {
        reader: BufReader<fs::File>,
        offset: u64,
        number: usize,
    },
    Converted(std::vec::IntoIter<TurnRecord>),
    Empty,
}

impl Source {
    pub fn next(&mut self, conversation_id: &str) -> Result<Option<(TurnRecord, u64)>, Error> {
        match self {
            Self::File {
                reader,
                offset,
                number,
            } => {
                let mut line = String::new();
                let read = reader.read_line(&mut line)? as u64;
                if read == 0 {
                    return Ok(None);
                }
                let at = *offset;
                (*offset, *number) = (at + read, *number + 1);
                Ok(Some((
                    parse_line(line_text(&line), *number, conversation_id)?.0,
                    at,
                )))
            }
            Self::Converted(records) => Ok(records.next().map(|record| (record, 0))),
            Self::Empty => Ok(None),
        }
    }
}

/// A records file from `offset` when its line there holds record `seq`, else from its start.
pub fn open_at(path: &Path, offset: u64, seq: u64, conversation_id: &str) -> Result<Source, Error> {
    let gone = |error: io::Error| -> Error {
        match error.kind() {
            io::ErrorKind::NotFound => {
                Refused::format_unsupported(conversation_id, "a turn's records are gone").into()
            }
            _ => error.into(),
        }
    };
    let mut reader = BufReader::new(fs::File::open(path).map_err(gone)?);
    if offset > 0 {
        reader.seek(SeekFrom::Start(offset - 1))?;
        let mut before = [0_u8];
        let mut line = String::new();
        let boundary = reader.read(&mut before)? == 1 && before[0] == b'\n';
        let holds = boundary
            && reader.read_line(&mut line)? > 0
            && parse_line(line_text(&line), seq as usize + 1, conversation_id)
                .is_ok_and(|(record, _)| record.seq == seq);
        if holds {
            reader.seek(SeekFrom::Start(offset))?;
            return Ok(Source::File {
                reader,
                offset,
                number: seq as usize,
            });
        }
        reader.seek(SeekFrom::Start(0))?;
    }
    Ok(Source::File {
        reader,
        offset: 0,
        number: 0,
    })
}

/// Where the piece holding record `seq` begins, and its line's offset.
pub fn piece_start(
    mut source: Source,
    seq: u64,
    conversation_id: &str,
) -> Result<(u64, u64), Error> {
    let mut start = (seq, 0);
    while let Some((record, offset)) = source.next(conversation_id)? {
        if record.chunk == 0 {
            start = (record.seq, offset);
        }
        if record.seq >= seq {
            return Ok(start);
        }
    }
    Ok((seq, 0))
}

/// The turn log from a continuation's hint when its row holds the turn it
/// names, else from the start: the offset and the lines before it.
pub fn seek_log(
    reader: &mut BufReader<fs::File>,
    rows: &TurnReader,
    start: ReadPosition,
    conversation_id: &str,
) -> Result<(u64, u64), Error> {
    if start.log_offset > 0 && start.log_line > 1 {
        reader.seek(SeekFrom::Start(start.log_offset - 1))?;
        let mut before = [0_u8];
        let mut line = String::new();
        let boundary = reader.read(&mut before)? == 1 && before[0] == b'\n';
        let holds = boundary
            && reader.read_line(&mut line)? > 0
            && rows
                .row(line_text(&line), start.log_line as usize, conversation_id)
                .is_ok_and(|row| row.turn() == start.turn);
        if holds {
            reader.seek(SeekFrom::Start(start.log_offset))?;
            return Ok((start.log_offset, start.log_line - 1));
        }
    }
    reader.seek(SeekFrom::Start(0))?;
    Ok((0, 0))
}
