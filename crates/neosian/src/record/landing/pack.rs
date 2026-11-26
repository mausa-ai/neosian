//! A queued turn's parts (DESIGN §40.54 items 8.2, 9 and 17.3): its
//! records in `seq` order, packed into `write_records` bodies of at most
//! 983,040 bytes and 2,048 records. A body is measured with the bytes
//! `remote::records::encode::write` produces for it: the part's envelope
//! written with no records (`..."records":[]}`), plus `encode::record` of
//! each record, plus one comma between two records. The opening part, the
//! one that begins at `seq` 0, names the actor and states `turn_bytes`, the
//! sum of `size` over the turn's pieces; later parts name neither; only the
//! last part seals. A part is closed before the record that would take it
//! past either bound and holds at least one record; then its body is built
//! and measured again, and a part over a bound is refused, never handed out.

use crate::conversation::turn_records::{TurnRecord, Write};
use crate::remote::records::encode;

/// The most bytes of one part's body the binary sends (item 8.2).
pub const PART_BYTES: usize = 983_040;
/// The most records one part carries (item 9).
pub const PART_RECORDS: usize = 2_048;

/// What a sealed header's `bytes` will be: the sum of `size` over the
/// turn's pieces, each of which has exactly one record with chunk 0.
pub fn turn_bytes(records: &[TurnRecord]) -> u64 {
    records
        .iter()
        .filter(|record| record.chunk == 0)
        .map(|record| record.size)
        .sum()
}

/// What every request of one draft names beside its records.
pub struct Draft<'a> {
    pub conversation: &'a str,
    pub draft: &'a str,
    pub actor: &'a str,
    pub turn_bytes: u64,
}

impl Draft<'_> {
    /// One part: the opening names the actor and the total, the last seals.
    pub fn part(&self, records: Vec<TurnRecord>, opening: bool, seal: bool) -> Write {
        Write {
            draft: self.draft.to_string(),
            records,
            turn_bytes: opening.then_some(self.turn_bytes),
            seal,
            discard: false,
            actor: opening.then(|| self.actor.to_string()),
            stamp: None,
        }
    }

    /// A probe: the draft's state, nothing created.
    pub fn probe(&self) -> Write {
        self.part(Vec::new(), false, false)
    }

    /// The draft dropped with its records.
    pub fn discard(&self) -> Write {
        Write {
            discard: true,
            ..self.probe()
        }
    }

    /// The bytes of a part's body as it is sent.
    fn body(&self, write: &Write) -> usize {
        encode::write(self.conversation, write).len()
    }
}

/// The final check: the part's body built by `encode::write` and measured,
/// refused over either bound.
pub fn check(conversation: &str, write: &Write) -> Result<(), String> {
    let bytes = encode::write(conversation, write).len();
    let records = write.records.len();
    if bytes > PART_BYTES || records > PART_RECORDS {
        return Err(format!(
            "a part of this turn would carry {bytes} bytes in {records} records, past the \
             {PART_BYTES} bytes and {PART_RECORDS} records a part may carry"
        ));
    }
    Ok(())
}

/// The parts of a turn from its first record not yet stored, built one at
/// a time; the records of a part handed out are no longer held here.
pub struct Parts<'a> {
    draft: &'a Draft<'a>,
    rest: Vec<TurnRecord>,
    opening: bool,
    done: bool,
}

impl<'a> Parts<'a> {
    /// The parts of `rest`, the turn's records from the first one the store
    /// does not hold yet; `opening` when that is `seq` 0. With nothing left,
    /// one part without records seals.
    pub fn new(draft: &'a Draft<'a>, rest: Vec<TurnRecord>, opening: bool) -> Self {
        Self {
            draft,
            rest,
            opening,
            done: false,
        }
    }

    /// How many of the remaining records the next part takes: the most, at
    /// most 2,048, whose body stays within 983,040 bytes, and at least one.
    fn fits(&self) -> usize {
        let envelope = |seal| {
            self.draft
                .body(&self.draft.part(Vec::new(), self.opening, seal))
        };
        let (open, sealing) = (envelope(false), envelope(true));
        let (mut records, mut count) = (0, 0);
        for (at, record) in self.rest.iter().enumerate().take(PART_RECORDS) {
            let grown = records + usize::from(at > 0) + encode::record(None, record).len();
            let envelope = if at + 1 == self.rest.len() {
                sealing
            } else {
                open
            };
            if at > 0 && envelope + grown > PART_BYTES {
                break;
            }
            (records, count) = (grown, at + 1);
        }
        count
    }
}

impl Iterator for Parts<'_> {
    type Item = Result<Write, String>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let count = self.fits();
        let seal = count == self.rest.len();
        let records: Vec<TurnRecord> = self.rest.drain(..count).collect();
        let write = self.draft.part(records, self.opening, seal);
        let checked = check(self.draft.conversation, &write).map(|()| write);
        self.opening = false;
        self.done = seal || checked.is_err();
        Some(checked)
    }
}
