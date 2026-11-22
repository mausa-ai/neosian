//! A record read on a FileStore (§40.54 item 10): the turn log from a
//! byte hint (trusted only where its row holds the turn it names), each
//! turn's records read a line at a time from its records file, or a whole
//! format-1 turn converted as it is reached, through the shared `Viewer`.
//! A page takes groups while their bytes stay within it, always one: a
//! turn's header goes with its first item, so every page moves on. Memory
//! follows the page: one record, a head's few bytes, and for a format-1
//! turn that turn whole, as its legacy read holds it.

use std::fs;
use std::io::{self, BufRead, BufReader};

use super::RECORD_FILES;
use super::source::{Page, Source, open_at, piece_start, seek_log};
use crate::conversation::rows::TURNS;
use crate::conversation::rows_header::Row;
use crate::conversation::search::line_text;
use crate::conversation::turn_records::{
    Header, ReadItem, ReadPosition, RecordRead, Viewer, records_of,
};
use crate::conversation::{Error, Refused};
use crate::store::FileStore;

impl FileStore {
    /// One page of a record read: its items to `visit` in turn order, each
    /// turn's header first; where the next page starts, None on the last.
    pub fn read_records(
        &self,
        conversation_id: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(ReadItem) -> Result<(), Error>,
    ) -> Result<Option<ReadPosition>, Error> {
        read.check().map_err(Error::Value)?;
        let _held = self.lock()?;
        let rows = self.turn_reader(conversation_id)?;
        let fingerprint = read.fingerprint(conversation_id);
        let (start, exact) = match read.continuation.as_deref() {
            Some(token) => (
                ReadPosition::decode(token, &fingerprint).map_err(Error::Value)?,
                true,
            ),
            None => {
                let (turn, seq) = read.start();
                (
                    ReadPosition {
                        turn,
                        seq,
                        ..ReadPosition::default()
                    },
                    false,
                )
            }
        };
        let file = self.conversation_file(conversation_id, TURNS)?;
        let mut reader = match fs::File::open(&file) {
            Ok(handle) => BufReader::new(handle),
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let (mut offset, mut number) = seek_log(&mut reader, &rows, start, conversation_id)?;
        let mut page = Page {
            limit: read.page_bytes,
            used: 0,
            count: 0,
        };
        let mut buffer = String::new();
        loop {
            buffer.clear();
            let size = reader.read_line(&mut buffer)? as u64;
            if size == 0 {
                return Ok(None);
            }
            let at = offset;
            (offset, number) = (offset + size, number + 1);
            let row = rows.row(line_text(&buffer), number as usize, conversation_id)?;
            let turn = row.turn();
            if turn < start.turn {
                continue;
            }
            if turn > read.last() {
                return Ok(None);
            }
            let (first, hint) = if turn == start.turn {
                (start.seq, start.record_offset)
            } else {
                (0, 0)
            };
            let place = Place { first, hint, exact };
            if let Some(next) =
                self.turn_page(conversation_id, row, read, place, &mut page, visit)?
            {
                return Ok(Some(ReadPosition {
                    turn,
                    log_offset: at,
                    log_line: number,
                    ..next
                }));
            }
        }
    }

    fn turn_page(
        &self,
        conversation_id: &str,
        row: Row,
        read: &RecordRead,
        place: Place,
        page: &mut Page,
        visit: &mut dyn FnMut(ReadItem) -> Result<(), Error>,
    ) -> Result<Option<ReadPosition>, Error> {
        let (header, mut source, from) = self.turn_source(conversation_id, row, read, place)?;
        let stop = |seq: u64, record_offset: u64| {
            Some(ReadPosition {
                seq,
                record_offset,
                ..ReadPosition::default()
            })
        };
        let mut viewer = Viewer::new(read, header.turn, from);
        let mut pending = Some(ReadItem::Header(header));
        while let Some((record, offset)) = source.next(conversation_id)? {
            for (item, _) in viewer.push(&record) {
                let ReadItem::Record {
                    record: ref held, ..
                } = item
                else {
                    continue;
                };
                let (seq, at) = (held.seq, if held.seq == record.seq { offset } else { 0 });
                let bytes = item.bytes() + pending.as_ref().map_or(0, ReadItem::bytes);
                if !page.admit(bytes) {
                    return Ok(if pending.is_some() {
                        stop(place.first, place.hint)
                    } else {
                        stop(seq, at)
                    });
                }
                if let Some(header) = pending.take() {
                    visit(header)?;
                }
                visit(item)?;
            }
            if viewer.done() {
                break;
            }
        }
        if let Some(header) = pending {
            if !page.admit(header.bytes()) {
                return Ok(stop(place.first, place.hint));
            }
            visit(header)?;
        }
        Ok(None)
    }

    /// A turn's header, its records' source, and the record the view starts at.
    fn turn_source(
        &self,
        conversation_id: &str,
        row: Row,
        read: &RecordRead,
        place: Place,
    ) -> Result<(Header, Source, u64), Error> {
        let fetch = read.piece && !place.exact;
        match row {
            Row::Whole(turn) if turn.redacted => {
                let header = Header {
                    redacted: true,
                    ..Header::of(turn.turn, turn.created_at, turn.actor, &[])
                };
                Ok((header, Source::Empty, place.first))
            }
            Row::Whole(turn) => {
                let refuse = |why: String| {
                    Refused::format_unsupported(
                        conversation_id,
                        &format!("turn {}: {why}", turn.turn),
                    )
                };
                let records = records_of(&turn.messages).map_err(refuse)?;
                let header = Header::of(turn.turn, turn.created_at, turn.actor.clone(), &records);
                let from = if fetch {
                    piece_start(
                        Source::Converted(records.clone().into_iter()),
                        place.first,
                        conversation_id,
                    )?
                    .0
                } else {
                    place.first
                };
                Ok((header, Source::Converted(records.into_iter()), from))
            }
            Row::Records(sealed) => {
                let path = self
                    .conversation_dir(conversation_id)?
                    .join(RECORD_FILES)
                    .join(format!("{}.jsonl", sealed.turn));
                let header = Header {
                    turn: sealed.turn,
                    created_at: sealed.created_at,
                    actor: sealed.actor,
                    redacted: false,
                    records: sealed.records,
                    bytes: sealed.bytes,
                };
                let (from, offset) = if fetch {
                    piece_start(
                        open_at(&path, 0, 0, conversation_id)?,
                        place.first,
                        conversation_id,
                    )?
                } else {
                    (place.first, place.hint)
                };
                Ok((header, open_at(&path, offset, from, conversation_id)?, from))
            }
        }
    }
}

/// Where a turn's part of the read begins: the record, a records-file hint,
/// and whether a continuation named it exactly.
#[derive(Clone, Copy)]
struct Place {
    first: u64,
    hint: u64,
    exact: bool,
}
