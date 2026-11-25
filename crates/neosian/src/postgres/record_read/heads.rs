//! Map selected pieces to primary-key intervals. A preview reads its text
//! from each edge, leaving the intervening TOAST values in the database.

use super::{Reader, TurnView};
use crate::conversation::Error;
use crate::conversation::turn_records::{
    Header, Kind, ReadItem, RecordRead, TurnRecord, encoded_len, head, tail,
};
use crate::postgres::connection::failure;
use crate::postgres::record_read::{damaged, signed};
use tokio_postgres::Row;

struct Piece {
    start: u64,
    last: u64,
    kind: Kind,
    bytes: u64,
}

impl Piece {
    fn from_last(row: &Row) -> Option<Self> {
        let last = u64::try_from(row.try_get::<_, i32>("seq").ok()?).ok()?;
        let chunk = u64::try_from(row.try_get::<_, i32>("chunk").ok()?).ok()?;
        Some(Self {
            start: last.checked_sub(chunk)?,
            last,
            kind: Kind::parse(row.try_get("kind").ok()?)?,
            bytes: u64::try_from(row.try_get::<_, i64>("size").ok()?).ok()?,
        })
    }
}

impl Reader<'_, '_> {
    /// Read at most the requested interval. Each edge uses a fetch of one:
    /// a preview must not pull in middle chunks through driver prefetch.
    async fn span(
        &self,
        turn: u64,
        piece: &Piece,
        range: std::ops::RangeInclusive<u64>,
        reverse: bool,
        batch: u64,
        mut visit: impl FnMut(TurnRecord) -> Result<bool, Error>,
    ) -> Result<bool, Error> {
        let (low, high) = range.into_inner();
        let mut visited = 0;
        let count = high - low + 1;
        while visited < count {
            // Tails advance one record backwards; every SQL range is ascending.
            let width = if reverse {
                1
            } else {
                batch.min(count - visited)
            };
            let first = if reverse {
                high - visited
            } else {
                low + visited
            };
            let more = self
                .bodies()
                .interval(turn, first, first + width, |r| {
                    if r.chunk != r.seq - piece.start
                        || r.kind != piece.kind
                        || r.size != piece.bytes
                    {
                        return Err(damaged(
                            self.id,
                            turn,
                            &format!("record {} is unreadable", r.seq),
                        ));
                    }
                    visit(r)
                })
                .await?;
            if !more {
                return Ok(false);
            }
            visited += width;
        }
        Ok(true)
    }

    /// A piece known to exceed the preview threshold from its raw bytes.
    /// Legal noncanonical chunk boundaries can require several rows per edge.
    async fn preview(&self, turn: u64, piece: &Piece, limit: usize) -> Result<ReadItem, Error> {
        let mut first = None;
        let mut prefix = String::new();
        let mut prefix_bytes = 2; // The quotes in the JSON string encoding.
        self.span(turn, piece, piece.start..=piece.last, false, 1, |record| {
            prefix_bytes += encoded_len(&record.text) - 2;
            prefix.push_str(&record.text);
            first.get_or_insert(record);
            Ok(prefix_bytes < limit)
        })
        .await?;
        let mut record = first.ok_or_else(|| damaged(self.id, turn, "missing first chunk"))?;
        record.text = head(&prefix, limit).to_owned();
        let mut trailing = Vec::new();
        let mut tail_bytes = 2;
        if tail_bytes < limit {
            self.span(turn, piece, piece.start..=piece.last, true, 1, |r| {
                tail_bytes += encoded_len(&r.text) - 2;
                trailing.extend(r.text.bytes().rev());
                Ok(tail_bytes < limit)
            })
            .await?;
        }
        trailing.reverse();
        let suffix = std::str::from_utf8(&trailing).expect("complete UTF-8 chunks");
        Ok(ReadItem::Record {
            turn,
            record,
            tail: Some(tail(suffix, limit).to_owned()),
            truncated: true,
        })
    }

    pub(super) async fn view_records(
        &self,
        header: &Header,
        from: u64,
        read: &RecordRead,
        view: &mut TurnView<'_, '_, '_>,
    ) -> Result<(), Error> {
        if read.piece || (read.heads.is_empty() && read.kinds.is_none()) {
            return self.bodies().records(header, from, |r| view.push(&r)).await;
        }
        if from >= header.records {
            return Ok(());
        }
        let turn = header.turn;
        let invalid = || damaged(self.id, turn, "piece bounds do not match its header");
        let mut seq = from;
        let mut window = from;
        while window < header.records {
            // Even a generic plan that sorts before answering can inspect
            // at most this many sequence positions ahead of the page.
            let until = (window + 128).min(header.records);
            let ends = self
                .tx
                .query(
                    &self.statements.head_scan,
                    &[&self.id, &(turn as i32), &signed(window), &signed(until)],
                )
                .await
                .map_err(failure)?;
            for row in ends {
                let piece = Piece::from_last(&row).ok_or_else(invalid)?;
                if piece.last < seq
                    || piece.last >= header.records
                    || piece.start > seq
                    || (seq != from && piece.start != seq)
                {
                    return Err(invalid());
                }
                if read.selects(piece.kind) {
                    let preview = read.head(piece.kind).filter(|&limit| {
                        seq == piece.start
                            && piece.start != piece.last
                            && piece.bytes.saturating_add(2) > 2 * limit as u64
                    });
                    let more = match preview {
                        Some(limit) => view.emit(self.preview(turn, &piece, limit).await?)?,
                        None => {
                            self.span(turn, &piece, seq..=piece.last, false, 8, |r| view.push(&r))
                                .await?
                        }
                    };
                    if !more {
                        return Ok(());
                    }
                }
                seq = piece.last + 1;
            }
            window = until;
        }
        if seq != header.records {
            return Err(invalid());
        }
        Ok(())
    }
}
