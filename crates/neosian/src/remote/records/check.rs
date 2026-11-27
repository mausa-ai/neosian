//! Each `read_records` page checked before a door sees it (DESIGN §40.54
//! item 10; the T1fe rulings J2 and J4). Across the pages of one call a
//! continuation is bounded and names a new place, a header's turn never
//! goes down and a record's turn and `seq` strictly go up. Against the
//! request: every header and record lies in the turns asked for, and in a
//! piece fetch in that one piece; a record comes under its turn's header on
//! the same page and never under a redacted one; its kind is one asked
//! for; a head is no longer than asked, and a piece the request heads is
//! answered whole only when it fits twice the head. What a conforming
//! server sends passes: a header repeated on the page after one that ended
//! inside its turn, a redacted header with no records, a head with its
//! tail, and the remaining chunks of a piece the read starts inside.
//!
//! A page that names a continuation moves on: it holds a record or a
//! header of a turn not met yet. A store may answer one page holding only
//! the repeated header of the turn the last page ended in, when that
//! turn's remaining records are not asked for and the next turn's group no
//! longer fits (`store/records/read.rs`); the page after it then starts at
//! a later turn. So one such page passes and a second in a row is refused.

use super::{Failure, RecordPage};
use crate::conversation::turn_records::{RecordRead, encoded_len};

fn bad(what: &str) -> Failure {
    Failure::Unreadable(what.to_string())
}

/// What one read has met so far.
pub struct Checked<'a> {
    read: &'a RecordRead,
    /// The highest header's turn.
    reached: u64,
    /// The last record's turn and `seq`.
    passed: Option<(u64, u64)>,
    /// Whether the last page moved on.
    moved: bool,
    /// In a piece fetch, the first record of the piece answered.
    piece: Option<u64>,
}

impl<'a> Checked<'a> {
    pub fn new(read: &'a RecordRead) -> Self {
        Self {
            read,
            reached: 0,
            passed: None,
            moved: true,
            piece: None,
        }
    }

    /// `page`, answered to a request that sent `sent` as its continuation.
    pub fn page(&mut self, page: &RecordPage, sent: Option<&str>) -> Result<(), Failure> {
        let next = page.continuation.as_deref();
        // A continuation is bounded, names a new place and follows a page
        // that held something, or the read would never end.
        let empty = page.turns.is_empty() && page.records.is_empty();
        let sound = next
            .is_none_or(|token| !empty && (1..=4096).contains(&token.len()) && sent != Some(token));
        if !sound {
            return Err(bad("continuation"));
        }
        let before = self.reached;
        for header in &page.turns {
            if header.turn < self.reached {
                return Err(bad("turns go backwards"));
            }
            self.reached = header.turn;
        }
        for one in &page.records {
            let place = (one.turn, one.record.seq);
            if self.passed.is_some_and(|passed| place <= passed) {
                return Err(bad("records go backwards"));
            }
            self.passed = Some(place);
        }
        self.asked(page)?;
        let moved = !page.records.is_empty() || self.reached > before;
        if next.is_some() && !moved && !self.moved {
            return Err(bad("a page that does not move on"));
        }
        self.moved = moved;
        Ok(())
    }

    /// The page against the request itself.
    fn asked(&mut self, page: &RecordPage) -> Result<(), Failure> {
        let read = self.read;
        let first = read.turn.unwrap_or(read.after.saturating_add(1));
        let inside = |turn: u64| (first..=read.last()).contains(&turn);
        if page.turns.iter().any(|header| !inside(header.turn)) {
            return Err(bad("a turn outside the turns asked"));
        }
        for one in &page.records {
            let record = &one.record;
            if !inside(one.turn) {
                return Err(bad("a record outside the turns asked"));
            }
            let Some(header) = page.turns.iter().find(|header| header.turn == one.turn) else {
                return Err(bad("a record without its turn's header"));
            };
            if header.redacted {
                return Err(bad("a record of a redacted turn"));
            }
            if !read.selects(record.kind) {
                return Err(bad("a record of a kind not asked"));
            }
            let start = record.seq.checked_sub(record.chunk);
            match (read.seq, read.piece) {
                (Some(seq), true) => {
                    let held = *self.piece.get_or_insert(start.unwrap_or(u64::MAX));
                    if start != Some(held) || held > seq {
                        return Err(bad("a record outside the piece asked"));
                    }
                }
                (Some(seq), false) if record.seq < seq => {
                    return Err(bad("a record before the seq asked"));
                }
                _ => {}
            }
            self.head(one)?;
        }
        Ok(())
    }

    /// A head within the bytes asked, and a piece answered whole only when
    /// its head would not have cut it.
    fn head(&self, one: &super::Answered) -> Result<(), Failure> {
        let record = &one.record;
        let head = if self.read.piece {
            None
        } else {
            self.read.head(record.kind)
        };
        let Some(bytes) = head else {
            return match (one.truncated, &one.tail) {
                (false, None) => Ok(()),
                _ => Err(bad("a head of a piece read whole")),
            };
        };
        // A head of one byte holds no text at all.
        let fits = |text: &str| text.is_empty() || encoded_len(text) <= bytes;
        match (one.truncated, one.tail.as_deref()) {
            (true, Some(tail)) if record.chunk == 0 && fits(&record.text) && fits(tail) => Ok(()),
            (true, _) => Err(bad("a head longer than asked")),
            (false, Some(_)) => Err(bad("a tail without a head")),
            // A piece's text encodes to at least its bytes and two quotes.
            (false, None)
                if record.chunk == 0 && record.size.saturating_add(2) > 2 * bytes as u64 =>
            {
                Err(bad("a piece longer than its head"))
            }
            (false, None) => Ok(()),
        }
    }
}
