//! Where a recalled turn's records come from (DESIGN §40.54 items 10 and
//! 15.2): a store that keeps records, read a page at a time with the
//! request's kinds and heads, or one turn read whole from any other store
//! and converted to records in memory, then answered by the same viewer
//! the stores answer with. Either way a reader takes one item at a time,
//! each header once, and can stop after any item.

use crate::conversation::Error;
use crate::conversation::turn_records::{Header, ReadItem, RecordRead, TurnRecord, Viewer};
use crate::remote::records::Answered;
use crate::state::records::{Fault, Records};

pub enum Origin<'a> {
    /// A store that keeps records, under whatever authority it carries.
    Kept {
        records: &'a dyn Records,
        conversation: &'a str,
    },
    /// One turn read whole and converted (format 1, or a legacy server).
    Whole {
        header: Header,
        records: Vec<TurnRecord>,
    },
}

/// One item of a read: a turn's header or a record as answered.
pub enum Item {
    Header(Header),
    Record(Answered),
}

impl Origin<'_> {
    /// The items `read` answers, in order; `visit` says whether to go on.
    /// A header a page repeats is handed on once.
    pub fn read(
        &self,
        read: &RecordRead,
        visit: &mut dyn FnMut(Item) -> Result<bool, Fault>,
    ) -> Result<(), Fault> {
        match self {
            Self::Kept {
                records,
                conversation,
            } => {
                let mut seen = 0;
                records.read(conversation, read, &mut |page| {
                    for header in page.turns {
                        if header.turn <= seen {
                            continue;
                        }
                        seen = header.turn;
                        if !visit(Item::Header(header))? {
                            return Ok(false);
                        }
                    }
                    for record in page.records {
                        if !visit(Item::Record(record))? {
                            return Ok(false);
                        }
                    }
                    Ok(true)
                })
            }
            Self::Whole { header, records } => whole(header, records, read, visit),
        }
    }
}

/// The converted turn as a store would answer `read`: its header when the
/// read reaches it, then each item its viewer completes.
fn whole(
    header: &Header,
    records: &[TurnRecord],
    read: &RecordRead,
    visit: &mut dyn FnMut(Item) -> Result<bool, Fault>,
) -> Result<(), Fault> {
    read.check().map_err(Error::Value)?;
    let (first, seq) = read.start();
    if header.turn < first || header.turn > read.last() {
        return Ok(());
    }
    if !visit(Item::Header(header.clone()))? {
        return Ok(());
    }
    let start = if header.turn == first { seq } else { 0 };
    let mut viewer = Viewer::new(read, header.turn, start);
    for record in records {
        for (item, _) in viewer.push(record) {
            let ReadItem::Record {
                turn,
                record,
                tail,
                truncated,
            } = item
            else {
                continue;
            };
            let answered = Answered {
                turn,
                record,
                tail,
                truncated,
            };
            if !visit(Item::Record(answered))? {
                return Ok(());
            }
        }
        if viewer.done() {
            break;
        }
    }
    Ok(())
}
