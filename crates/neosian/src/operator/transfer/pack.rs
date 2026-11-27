//! Restore parts (DESIGN §40.54 items 8.2, 12 and 22): the stream's items,
//! in order, packed into `store/restore_records` bodies of at most 983,040
//! bytes and 2,048 items. A body is measured with the bytes
//! `remote::records::encode::restore` produces for it: the request with no
//! items, plus for each list its key, brackets and place (`_from`) when the
//! list opens, plus each item's own encoding and one comma between two
//! items. The last item is known only when the next one arrives, so one
//! item waits; the part that takes the last item commits, and its
//! `"commit":true` is counted too. A part closes before the item that would
//! take it past either bound, and where the items the server already holds
//! end (`replay`), so a part either repeats what is stored or brings only
//! new items. Before a part leaves, its body is built and measured again,
//! and a part over a bound is refused, never sent. An item that cannot fit
//! a part alone is refused by name.

use super::stream::Piece;
use crate::conversation::Error;
use crate::conversation::turn_records::{Position, Restore};
use crate::remote::records::encode;

/// The most bytes of one restore part's body the binary sends (item 8.2).
pub(super) const PART_BYTES: usize = 983_040;
/// The most items one restore part carries (item 8.2).
pub(super) const PART_ITEMS: usize = 2_048;

/// One request of a restore, with what it is for.
pub(super) struct Part {
    pub restore: Restore,
    /// Every item is one the server already holds.
    pub replay: bool,
    /// The position after the part's last item.
    pub end: Position,
    /// The body's bytes as the packer measured them.
    pub bytes: usize,
}

/// The final check: the part's body built by the encoder that sends it,
/// measured, and refused over either bound or when the packer measured it
/// otherwise.
pub(super) fn check(conversation: &str, part: &Part) -> Result<(), Error> {
    let bytes = encode::restore(conversation, &part.restore).len();
    let items = part.restore.items_count();
    if bytes > PART_BYTES || items > PART_ITEMS {
        return Err(Error::Value(format!(
            "conversation {conversation}: a restore part would carry {bytes} bytes in {items} \
             items, past the {PART_BYTES} bytes and {PART_ITEMS} items a part may carry"
        )));
    }
    if bytes != part.bytes {
        return Err(Error::Value(format!(
            "conversation {conversation}: a restore part measured as {} bytes encodes to \
             {bytes}",
            part.bytes
        )));
    }
    Ok(())
}

/// An item's own encoding as a part carries it.
fn encoded(piece: &Piece) -> usize {
    match piece {
        Piece::Header(header) => encode::header(header).len(),
        Piece::Record(turn, record) => encode::record(Some(*turn), record).len(),
        Piece::Projection(_, entry) => encode::entry(entry).len(),
        Piece::Act(_, act) => encode::act(act).len(),
    }
}

/// Packs one conversation's stream into parts as it passes.
pub(super) struct Packer<'a> {
    conversation: &'a str,
    /// The body of a request with no items.
    base: usize,
    /// What `"commit":true` adds.
    commit: usize,
    part: Restore,
    bytes: usize,
    replay: bool,
    end: Position,
    waiting: Option<(Piece, Position, bool)>,
}

impl<'a> Packer<'a> {
    pub fn new(conversation: &'a str, restore: &str) -> Self {
        let empty = Restore {
            restore: restore.to_string(),
            ..Restore::default()
        };
        let base = encode::restore(conversation, &empty).len();
        let committing = Restore {
            commit: true,
            ..empty.clone()
        };
        let commit = encode::restore(conversation, &committing).len() - base;
        Self {
            conversation,
            base,
            commit,
            part: empty,
            bytes: base,
            replay: false,
            end: Position::default(),
            waiting: None,
        }
    }

    /// Takes the next item, `replay` when the server already holds it, and
    /// answers the parts this closed.
    pub fn push(&mut self, piece: Piece, end: Position, replay: bool) -> Result<Vec<Part>, Error> {
        let mut closed = Vec::new();
        if let Some((before, at, held)) = self.waiting.replace((piece, end, replay)) {
            closed.extend(self.add(before, at, held, false)?);
            if held && !replay && self.part.items_count() > 0 {
                closed.push(self.close());
            }
        }
        Ok(closed)
    }

    /// The parts left, the last of them committing. A stream always has an
    /// item, so the commit always carries one.
    pub fn finish(mut self) -> Result<Vec<Part>, Error> {
        let mut closed = Vec::new();
        if let Some((last, at, held)) = self.waiting.take() {
            closed.extend(self.add(last, at, held, true)?);
        }
        if self.part.items_count() > 0 {
            self.part.commit = true;
            closed.push(self.close());
        }
        Ok(closed)
    }

    /// What `piece` adds to the current part: its encoding, a comma after
    /// an item of its list, or the list's key, brackets and place when it
    /// opens the list (measured by encoding the list with this item alone).
    fn growth(&self, piece: &Piece) -> usize {
        let opens = match piece {
            Piece::Header(_) => self.part.turns.is_empty(),
            Piece::Record(..) => self.part.records.is_empty(),
            Piece::Projection(..) => self.part.projections.is_empty(),
            Piece::Act(..) => self.part.redactions.is_empty(),
        };
        if !opens {
            return 1 + encoded(piece);
        }
        let mut alone = Restore {
            restore: self.part.restore.clone(),
            ..Restore::default()
        };
        match piece {
            Piece::Header(header) => alone.turns.push(header.clone()),
            Piece::Record(turn, record) => alone.records.push((*turn, record.clone())),
            Piece::Projection(index, entry) => {
                alone.projections_from = Some(*index);
                alone.projections.push(entry.clone());
            }
            Piece::Act(index, act) => {
                alone.redactions_from = Some(*index);
                alone.redactions.push(act.clone());
            }
        }
        encode::restore(self.conversation, &alone).len() - self.base
    }

    fn add(
        &mut self,
        piece: Piece,
        end: Position,
        replay: bool,
        last: bool,
    ) -> Result<Option<Part>, Error> {
        let commit = if last { self.commit } else { 0 };
        let mut growth = self.growth(&piece);
        let items = self.part.items_count();
        let mut closed = None;
        if items > 0 && (self.bytes + growth + commit > PART_BYTES || items + 1 > PART_ITEMS) {
            closed = Some(self.close());
            growth = self.growth(&piece);
        }
        if self.base + growth + commit > PART_BYTES {
            return Err(self.unfit(&piece, self.base + growth + commit));
        }
        self.bytes += growth;
        (self.replay, self.end) = (replay, end);
        match piece {
            Piece::Header(header) => self.part.turns.push(header),
            Piece::Record(turn, record) => self.part.records.push((turn, record)),
            Piece::Projection(index, entry) => {
                self.part.projections_from.get_or_insert(index);
                self.part.projections.push(entry);
            }
            Piece::Act(index, act) => {
                self.part.redactions_from.get_or_insert(index);
                self.part.redactions.push(act);
            }
        }
        Ok(closed)
    }

    fn close(&mut self) -> Part {
        let empty = Restore {
            restore: self.part.restore.clone(),
            ..Restore::default()
        };
        let commit = if self.part.commit { self.commit } else { 0 };
        let bytes = std::mem::replace(&mut self.bytes, self.base) + commit;
        Part {
            restore: std::mem::replace(&mut self.part, empty),
            replay: self.replay,
            end: self.end,
            bytes,
        }
    }

    fn unfit(&self, piece: &Piece, bytes: usize) -> Error {
        let what = match piece {
            Piece::Header(header) => format!("the header of turn {}", header.turn),
            Piece::Record(turn, record) => format!("record {} of turn {turn}", record.seq),
            Piece::Projection(index, _) => format!("projection {index}"),
            Piece::Act(index, _) => format!("erasure act {index}"),
        };
        Error::Value(format!(
            "conversation {}: {what} needs a restore part of {bytes} bytes, past the {PART_BYTES} \
             bytes a part may carry, so this conversation cannot be restored in parts",
            self.conversation
        ))
    }
}
