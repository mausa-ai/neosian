//! The newest turns of a conversation on a store that keeps records, read
//! as `walk` reads them but only as far back as a byte window allows (the
//! left-off block, DESIGN §40.54 item 19, under the session start's 50 ms).
//! A FileStore scans a turn's records whole even for heads, so the window
//! counts each turn's stored bytes as its header gives them: the headers
//! come first, then the view through the last of them, starting after the
//! newest turns whose bytes fit. The older turns are never read; the walk
//! names them so the block folds them into its count line.

use super::{Builder, Ends, Preview, Turns, Walked, error, request};
use crate::conversation::Error;
use crate::conversation::recall::origin::{Item, Origin};
use crate::conversation::recall::unconvertible;
use crate::conversation::turn_records::RecordRead;
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
    conversation: &str,
    builder: &mut Builder,
    bytes: u64,
    visit: &mut dyn FnMut(Preview) -> Result<(), Fault>,
) -> Result<(Walked, Option<Skipped>), Error> {
    match newest(records, conversation, builder, bytes, visit) {
        Err(fault) if unconvertible(&fault) => Ok((Walked::Unconvertible, None)),
        Err(fault) => Err(error(fault)),
        Ok(walked) => Ok(walked),
    }
}

fn newest(
    records: &dyn Records,
    conversation: &str,
    builder: &mut Builder,
    bytes: u64,
    visit: &mut dyn FnMut(Preview) -> Result<(), Fault>,
) -> Result<(Walked, Option<Skipped>), Fault> {
    let origin = Origin::Kept {
        records,
        conversation,
    };
    let headers = RecordRead {
        kinds: Some(Vec::new()),
        ..RecordRead::default()
    };
    let (mut sizes, mut first, mut last) = (Vec::new(), None, None);
    origin.read(&headers, &mut |item| {
        if let Item::Header(header) = item {
            sizes.push((header.turn, header.bytes));
            first.get_or_insert_with(|| header.clone());
            last = Some(header);
        }
        Ok(true)
    })?;
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
        origin.read(&read, &mut |item| turns.item(item))?;
        turns.end()?;
    }
    let skipped = (after >= first.turn).then_some(Skipped {
        first: first.turn,
        last: after,
    });
    Ok((Walked::Read(Ends { first, last }), skipped))
}
