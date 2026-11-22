//! The rules of one restore stream (§40.54 item 12): every item has a
//! place; an item at a place already stored repeats it (the store compares
//! it, `restore_mismatch` when it differs) and changes nothing; an item past
//! the next place is `restore_gap`; a new item moves the position. Each
//! turn's records pass the draft check, its header's `bytes` stated as the
//! total, so a turn is complete only when its records end whole and hold
//! exactly the header's bytes. The unit digest runs as the stream passes
//! and is kept with the position, a few hundred bytes, for the commit.
//! Serializable: a store keeps it in the restore's state between parts.

use serde::{Deserialize, Serialize};

use super::{Item, Position};
use crate::conversation::turn_records::{DraftCheck, Refusal, UnitDigest};

/// The current turn's header, as far as its records need it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
struct Current {
    records: u64,
    bytes: u64,
}

/// What an admitted item is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Placed {
    /// A place not yet stored: append it.
    New,
    /// A place already stored: compare it, change nothing.
    Repeat,
}

/// Why an item was refused.
#[derive(Debug, PartialEq, Eq)]
pub enum StreamRefusal {
    /// Past the next place (`restore_gap`).
    Gap,
    /// Out of its bounds or its order: the request's fault.
    Invalid(String),
    /// A header past the store's bound on one turn (item 8.2).
    Bound { bytes: u64, maximum: u64 },
}

/// One restore stream's state between parts.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RestoreStream {
    position: Position,
    current: Option<Current>,
    check: DraftCheck,
    digest: UnitDigest,
}

fn invalid(why: String) -> StreamRefusal {
    StreamRefusal::Invalid(why)
}

impl RestoreStream {
    pub fn position(&self) -> Position {
        self.position
    }

    /// The unit digest of the items admitted so far (the commit's, T1b2b2).
    pub fn digest(&self) -> String {
        self.digest.clone().finish()
    }

    /// The next item. `maximum` is `NEOSIAN_MAX_RECORD_TURN_BYTES`. A
    /// refused item changes nothing; a store clones the stream before a
    /// request and keeps the clone only when every item passed.
    pub fn admit(&mut self, item: &Item<'_>, maximum: u64) -> Result<Placed, StreamRefusal> {
        let at = self.position;
        match *item {
            Item::Header(header) => {
                if header.turn <= at.turn {
                    return Ok(Placed::Repeat);
                }
                if at.projections > 0 || at.redactions > 0 {
                    return Err(invalid(format!(
                        "turn {}: turns come before projections and trail acts",
                        header.turn
                    )));
                }
                if !at.complete {
                    return Err(StreamRefusal::Gap);
                }
                if header.records == 0 && header.bytes != 0 {
                    return Err(invalid(format!(
                        "turn {}: a turn without records holds no bytes",
                        header.turn
                    )));
                }
                if header.bytes > maximum {
                    let bytes = header.bytes;
                    return Err(StreamRefusal::Bound { bytes, maximum });
                }
                self.position = Position {
                    turn: header.turn,
                    seq: 0,
                    complete: header.records == 0,
                    ..at
                };
                self.current = Some(Current {
                    records: header.records,
                    bytes: header.bytes,
                });
                self.check = DraftCheck::default();
                self.digest.header(header);
            }
            Item::Record(turn, record) => {
                if turn < at.turn || (turn == at.turn && record.seq < at.seq) {
                    return Ok(Placed::Repeat);
                }
                let current = self.current.filter(|_| turn == at.turn);
                let Some(current) = current.filter(|_| record.seq == at.seq) else {
                    return Err(StreamRefusal::Gap);
                };
                if at.complete {
                    return Err(invalid(format!(
                        "turn {turn}: record {} is past its header's {} records",
                        record.seq, current.records
                    )));
                }
                self.check
                    .admit(record, Some(current.bytes), maximum)
                    .map_err(|refusal| match refusal {
                        Refusal::Invalid(why) => invalid(format!("turn {turn}: {why}")),
                        Refusal::Stated { bytes, .. } => invalid(format!(
                            "turn {turn}: its records reach {bytes} bytes, past its header's {}",
                            current.bytes
                        )),
                        Refusal::Bound { bytes, maximum } => {
                            StreamRefusal::Bound { bytes, maximum }
                        }
                    })?;
                let seq = at
                    .seq
                    .checked_add(1)
                    .ok_or_else(|| invalid("seq overflows".into()))?;
                let complete = seq == current.records;
                if complete {
                    self.check
                        .sealable()
                        .map_err(|why| invalid(format!("turn {turn}: {why}")))?;
                    if self.check.bytes() != current.bytes {
                        return Err(invalid(format!(
                            "turn {turn}: its records hold {} bytes where its header says {}",
                            self.check.bytes(),
                            current.bytes
                        )));
                    }
                }
                self.position = Position {
                    seq,
                    complete,
                    ..at
                };
                self.digest.record(turn, record);
            }
            Item::Projection(index, entry) => {
                if index < at.projections {
                    return Ok(Placed::Repeat);
                }
                if index > at.projections || !at.complete {
                    return Err(StreamRefusal::Gap);
                }
                if at.redactions > 0 {
                    return Err(invalid("projections come before trail acts".into()));
                }
                self.position.projections = index + 1;
                self.digest.projection(entry);
            }
            Item::Act(index, act) => {
                if index < at.redactions {
                    return Ok(Placed::Repeat);
                }
                if index > at.redactions || !at.complete {
                    return Err(StreamRefusal::Gap);
                }
                self.position.redactions = index + 1;
                self.digest.act(act);
            }
        }
        Ok(Placed::New)
    }
}
