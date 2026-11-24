//! A record page groups each turn's header with its first selected item.
//! A continuation resumes by (turn, seq), ignoring FileStore byte hints.
#[path = "heads.rs"]
mod heads;

use super::{PostgresStore, Reader, Stored, damaged};
use crate::conversation::Error;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::turn_records::{
    Header, ReadItem, ReadPosition, RecordRead, TurnRecord, Viewer, records_of,
};
use crate::limits;
use crate::store::error::MemoryError;

struct Page<'a> {
    used: u64,
    limit: u64,
    row_budget: u64,
    visit: &'a mut dyn FnMut(ReadItem) -> Result<(), Error>,
}

impl Page<'_> {
    fn admit(&mut self, bytes: u64) -> bool {
        if self.used > 0 && self.used.saturating_add(bytes) > self.limit {
            return false;
        }
        self.used += bytes;
        true
    }
}

struct TurnView<'a, 'b, 'r> {
    page: &'a mut Page<'b>,
    viewer: Viewer<'r>,
    header: Option<ReadItem>,
    requested: u64,
    resume: Option<u64>,
}

impl TurnView<'_, '_, '_> {
    fn push(&mut self, record: &TurnRecord) -> Result<bool, Error> {
        for (item, _) in self.viewer.push(record) {
            if !self.emit(item)? {
                return Ok(false);
            }
        }
        Ok(!self.viewer.done())
    }

    fn emit(&mut self, item: ReadItem) -> Result<bool, Error> {
        let ReadItem::Record {
            record: ref held, ..
        } = item
        else {
            unreachable!("a projected item is a record")
        };
        let item_bytes = item.bytes();
        limits::check(limits::RESPONSE, item_bytes - 1, self.page.row_budget)?;
        let bytes = item_bytes + self.header.as_ref().map_or(0, ReadItem::bytes);
        if !self.page.admit(bytes) {
            self.resume = Some(if self.header.is_some() {
                self.requested
            } else {
                held.seq
            });
            return Ok(false);
        }
        if let Some(header) = self.header.take() {
            (self.page.visit)(header)?;
        }
        (self.page.visit)(item)?;
        Ok(true)
    }

    fn finish(self) -> Result<Option<u64>, Error> {
        if self.resume.is_some() {
            return Ok(self.resume);
        }
        if let Some(header) = self.header {
            if !self.page.admit(header.bytes()) {
                return Ok(Some(self.requested));
            }
            (self.page.visit)(header)?;
        }
        Ok(None)
    }
}

impl Reader<'_, '_> {
    async fn view(
        &self,
        row: Stored,
        read: &RecordRead,
        start: ReadPosition,
        page: &mut Page<'_>,
    ) -> Result<Option<ReadPosition>, Error> {
        let (header, converted) = match row {
            Stored::Whole(turn) => {
                let records =
                    records_of(&turn.messages).map_err(|why| damaged(self.id, turn.turn, &why))?;
                let mut header = Header::of(turn.turn, turn.created_at, turn.actor, &records);
                header.redacted = turn.redacted;
                (header, Some(records))
            }
            Stored::Records(header) => (header, None),
        };
        let turn = header.turn;
        let first = if turn == start.turn { start.seq } else { 0 };
        let headers_only = read.kinds.as_deref() == Some(&[]);
        let from = if !headers_only && read.piece && read.continuation.is_none() {
            match &converted {
                Some(rows) => rows
                    .get(usize::try_from(first).unwrap_or(usize::MAX))
                    .map_or(first, |r| r.seq - r.chunk),
                None => self.piece_start(turn, first).await?,
            }
        } else {
            first
        };
        let mut view = TurnView {
            page,
            viewer: Viewer::new(read, turn, from),
            header: Some(ReadItem::Header(header.clone())),
            requested: first,
            resume: None,
        };
        if let Some(records) = converted {
            for record in records {
                if !view.push(&record)? {
                    break;
                }
            }
        } else if !headers_only {
            self.view_records(&header, from, read, &mut view).await?;
        }
        Ok(view.finish()?.map(|seq| ReadPosition {
            turn,
            seq,
            ..ReadPosition::default()
        }))
    }
}

impl PostgresStore {
    /// One page of record headers and records in turn order, in a single snapshot.
    #[cfg_attr(not(test), allow(dead_code))] // Reached by the wire in T1d.
    pub fn read_records(
        &self,
        id: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(ReadItem) -> Result<(), Error>,
    ) -> Result<Option<ReadPosition>, Error> {
        parse_conversation_id(id)?;
        read.check().map_err(Error::Value)?;
        if !self.supports_records()? {
            return Err(MemoryError::configuration(
                "records on Postgres require operator-installed records extension 1",
            )
            .into());
        }
        let start = match read.continuation.as_deref() {
            Some(cursor) => {
                ReadPosition::decode(cursor, &read.fingerprint(id)).map_err(Error::Value)?
            }
            None => {
                let (turn, seq) = read.start();
                ReadPosition {
                    turn,
                    seq,
                    ..ReadPosition::default()
                }
            }
        };
        let budget = limits::response_bytes()?;
        let mut page = Page {
            used: 0,
            limit: read.page_bytes,
            row_budget: budget,
            visit,
        };
        self.pool.record_snapshot(async |tx, statements| {
            let db = Reader {
                tx,
                statements,
                id,
                budget,
            };
            let headers = db.headers(start.turn, read.last(), None, true).await?;
            while let Some(row) = db.next_header(&headers).await? {
                if let Some(next) = db.view(row, read, start, &mut page).await? {
                    return Ok(Some(next));
                }
            }
            Ok(None)
        })
    }
}
