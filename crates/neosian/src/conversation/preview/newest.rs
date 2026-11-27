//! The newest turns of a conversation on a store that keeps records, read
//! as `walk` reads them but only as far back as a byte window allows (the
//! left-off block, DESIGN §40.54 item 19, under the session start's 50 ms).
//! A FileStore scans a turn's records whole even for heads, so the window
//! counts each turn's stored bytes as its header gives them: the last turn
//! as the store numbers it first, then the headers through it, then the
//! view through it, starting after the newest turns whose bytes fit. Both
//! reads go around a turn records cannot carry (`around.rs`). The older
//! turns are never read; the walk names them so the block folds them into
//! its count line.

use super::around::{Around, Sink};
use super::{Builder, Ends, Preview, Turns, Walked, error, request};
use crate::conversation::Error;
use crate::conversation::recall::origin::Item;
use crate::conversation::turn_records::{Header, RecordRead};
use crate::state::State;
use crate::state::records::{Fault, Records};

/// The turns a walk left unread, `first` through `last`, oldest first.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Skipped {
    pub first: u64,
    pub last: u64,
}

/// Every turn of `conversation` among the newest whose stored bytes total
/// at most `bytes`, reduced by `builder` and handed to `visit`, and the
/// older turns it left unread.
pub fn walk_newest(
    records: &dyn Records,
    state: &dyn State,
    conversation: &str,
    builder: &mut Builder,
    bytes: u64,
    visit: &mut dyn FnMut(Preview) -> Result<(), Fault>,
) -> Result<(Walked, Option<Skipped>), Error> {
    newest(records, state, conversation, builder, bytes, visit).map_err(error)
}

/// The headers a walk reads first: each turn's stored bytes, a turn
/// records cannot carry counting none since its records are never read.
#[derive(Default)]
struct Sizes {
    sizes: Vec<(u64, u64)>,
    first: Option<Header>,
    last: Option<Header>,
}

impl Sink for Sizes {
    fn item(&mut self, item: Item) -> Result<(), Fault> {
        if let Item::Header(header) = item {
            self.whole(header)?;
        }
        Ok(())
    }

    fn whole(&mut self, header: Header) -> Result<(), Fault> {
        self.sizes.push((header.turn, header.bytes));
        self.first.get_or_insert_with(|| header.clone());
        self.last = Some(header);
        Ok(())
    }
}

fn newest(
    records: &dyn Records,
    state: &dyn State,
    conversation: &str,
    builder: &mut Builder,
    bytes: u64,
    visit: &mut dyn FnMut(Preview) -> Result<(), Fault>,
) -> Result<(Walked, Option<Skipped>), Fault> {
    let last = state.last_turn_number(conversation)?;
    if last == 0 {
        return Ok((Walked::Empty, None));
    }
    let mut around = Around::new(records, state, conversation);
    let headers = RecordRead {
        through: Some(last),
        kinds: Some(Vec::new()),
        ..RecordRead::default()
    };
    let mut found = Sizes::default();
    around.read(&headers, &mut found)?;
    let Sizes { sizes, first, last } = found;
    let (Some(first), Some(last)) = (first, last) else {
        return Ok((Walked::Empty, None));
    };
    let mut after = last.turn;
    let mut spent = 0u64;
    for (at, (_, size)) in sizes.iter().enumerate().rev() {
        spent = spent.saturating_add(*size);
        if spent > bytes {
            break;
        }
        after = at.checked_sub(1).map_or(0, |before| sizes[before].0);
    }
    if after < last.turn {
        let read = RecordRead {
            after,
            ..request(builder.view(), last.turn)
        };
        let mut turns = Turns::new(builder, visit);
        around.read(&read, &mut turns)?;
        turns.end()?;
    }
    let skipped = (after >= first.turn).then_some(Skipped {
        first: first.turn,
        last: after,
    });
    Ok((Walked::Read(Ends { first, last }), skipped))
}
