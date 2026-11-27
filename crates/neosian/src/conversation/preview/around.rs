//! A range of turns read from records around each turn records cannot
//! carry (DESIGN §40.54 item 10; the T1fe ruling J3). A store refuses a
//! read that reaches a turn stored whole whose call id or tool name is too
//! long, naming that turn. The read is then asked again in parts: the turns
//! before that turn, from the turn the refused page would have gone on in,
//! then the turns after it. The turn itself is handed on in its place by
//! its header alone, which its own legacy read gives; its records are never
//! read, and no read ever reaches past the range asked. Items a part
//! answers again (a turn's header, the records of the turn it starts in
//! that were already handed on) are passed over, so each is handed on once.
//! Any other refusal, a damaged record's included, stays a refusal.

use crate::conversation::Error;
use crate::conversation::ids::read_of;
use crate::conversation::recall::conversion;
use crate::conversation::recall::origin::{Item, Origin};
use crate::conversation::turn_records::{Header, RecordRead};
use crate::state::State;
use crate::state::records::{Fault, Records};

/// Where the items of a read around go.
pub trait Sink {
    /// The next item, in order.
    fn item(&mut self, item: Item) -> Result<(), Fault>;
    /// A turn records cannot carry, in its place: its header alone.
    fn whole(&mut self, header: Header) -> Result<(), Fault>;
}

/// One conversation's records, and its state for the header of a turn the
/// records cannot carry.
pub struct Around<'a> {
    records: &'a dyn Records,
    state: &'a dyn State,
    conversation: &'a str,
    /// The headers of such turns already read.
    known: Vec<Header>,
}

/// What a read around has handed on.
struct Met {
    /// The last header's turn.
    header: u64,
    /// The last record's turn and `seq`.
    record: Option<(u64, u64)>,
}

impl Met {
    /// Whether the last header's turn may still hold records not handed on.
    fn partial(&self) -> bool {
        self.record.is_some_and(|(turn, _)| turn == self.header)
    }

    /// `item` handed to `sink` unless it was handed on already.
    fn pass(&mut self, item: Item, sink: &mut dyn Sink) -> Result<bool, Fault> {
        match &item {
            Item::Header(header) if header.turn <= self.header => return Ok(true),
            Item::Header(header) => self.header = header.turn,
            Item::Record(answered) => {
                let place = (answered.turn, answered.record.seq);
                if self.record.is_some_and(|record| place <= record) {
                    return Ok(true);
                }
                self.record = Some(place);
            }
        }
        sink.item(item)?;
        Ok(true)
    }
}

impl<'a> Around<'a> {
    pub fn new(records: &'a dyn Records, state: &'a dyn State, conversation: &'a str) -> Self {
        Self {
            records,
            state,
            conversation,
            known: Vec::new(),
        }
    }

    /// The turns `read` names (`after` and `through`, never `turn`), each
    /// item to `sink`, each turn records cannot carry as its header alone.
    pub fn read(&mut self, read: &RecordRead, sink: &mut dyn Sink) -> Result<(), Fault> {
        let origin = Origin::Kept {
            records: self.records,
            conversation: self.conversation,
        };
        let last = read.last();
        let mut met = Met {
            header: read.after,
            record: None,
        };
        // The turns found that records cannot carry and not yet handed on,
        // the nearest last.
        let mut ahead: Vec<u64> = Vec::new();
        loop {
            let upper = ahead.last().map_or(last, |turn| turn - 1);
            // A turn a refused page went on in is read again from its start.
            let after = met.header - u64::from(met.partial());
            if after < upper {
                let part = RecordRead {
                    after,
                    through: Some(upper),
                    continuation: None,
                    ..read.clone()
                };
                if let Err(fault) = origin.read(&part, &mut |item| met.pass(item, sink)) {
                    match conversion(&fault) {
                        Some(turn) if turn > met.header && turn <= upper => ahead.push(turn),
                        _ => return Err(fault),
                    }
                    continue;
                }
            }
            let Some(turn) = ahead.pop() else {
                return Ok(());
            };
            sink.whole(self.header(turn)?)?;
            met = Met {
                header: turn,
                record: None,
            };
        }
    }

    /// The header of `turn`, a turn records cannot carry, from its own
    /// legacy read: no other turn is read.
    fn header(&mut self, turn: u64) -> Result<Header, Fault> {
        if let Some(known) = self.known.iter().find(|header| header.turn == turn) {
            return Ok(known.clone());
        }
        let (state, conversation) = (self.state, self.conversation);
        let after = i64::try_from(turn - 1).unwrap_or(i64::MAX);
        let mut found = None;
        state.read_turns_each(conversation, after, Some(1), &mut |one| {
            read_of(&one, conversation)?;
            found = (one.turn == turn).then_some(Header {
                turn,
                created_at: one.created_at,
                actor: one.actor,
                redacted: one.redacted,
                records: 0,
                bytes: 0,
            });
            Ok(false)
        })?;
        let header = found.ok_or_else(|| {
            let why = format!("malformed record read: turn {turn} was refused and is not there");
            Fault::Local(Error::Value(why))
        })?;
        self.known.push(header.clone());
        Ok(header)
    }
}
