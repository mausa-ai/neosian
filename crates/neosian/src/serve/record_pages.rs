//! `conversation/read_records` (DESIGN §40.54 item 10): one page of a
//! conversation's turns as records, under a view. The body is decoded
//! strictly, `page_bytes` is served at most `NEOSIAN_MAX_PAGE_BYTES`, and
//! the answer is `turns`, then `records`, then `continuation`, in that
//! order in its bytes. A store hands each turn's header with that turn's
//! records, so the headers are written into the answer's spool as they
//! come and the records into a second one, joined behind them once the
//! store is done: neither is held whole, and nothing is sent before the
//! store is released.

use serde_json::{Value, json};

use super::backend::Backend;
use super::envelope::Fail;
use super::params::Payload;
use super::record_kinds::{kind, kinds};
use super::routes_records::kept;
use super::spool::Writer;
use super::strict::Strict;
use crate::conversation::turn_records::{Kind, ReadItem, RecordRead};
use crate::limits;
use crate::pyfmt::dumps;

const FIELDS: [&str; 10] = [
    "conversation_id",
    "turn",
    "after",
    "through",
    "seq",
    "piece",
    "kinds",
    "heads",
    "page_bytes",
    "continuation",
];
/// The longest continuation a request may send back, in bytes.
const CONTINUATION_BYTES: usize = 4_096;

/// `heads`: each kind once (a JSON object's keys) with its bytes; a value
/// that is no integer is refused as one outside the range is.
fn heads(body: &Strict) -> Result<Vec<(Kind, usize)>, Fail> {
    let Some(named) = body.object("heads")? else {
        return Ok(Vec::new());
    };
    let head = |(name, bytes): (&String, &Value)| {
        let kind = kind(name)?;
        let bytes = bytes.as_u64().map_or(0, |bytes| bytes as usize);
        Ok((kind, bytes))
    };
    named.iter().map(head).collect()
}

/// The request as the stores take it, every rule of the table applied.
fn selection(payload: &Payload, cap: u64) -> Result<(&str, RecordRead), Fail> {
    let body = Strict::new(payload, &FIELDS)?;
    let conversation_id = body.string("conversation_id")?;
    let turn = body.unsigned("turn")?;
    let whole = RecordRead::default();
    let read = RecordRead {
        turn,
        after: body.unsigned("after")?.unwrap_or(whole.after),
        through: body.unsigned("through")?,
        seq: body.unsigned("seq")?,
        piece: body.boolean("piece", whole.piece)?,
        kinds: kinds(&body, "kinds")?,
        heads: heads(&body)?,
        page_bytes: body.unsigned("page_bytes")?.unwrap_or(whole.page_bytes),
        continuation: body.optional_string("continuation")?.map(str::to_string),
    };
    // A named field counts even at its default: `after: 0` beside `turn`
    // is excluded like any other value, and `piece: false` needs what
    // `piece` needs.
    if turn.is_some() && body.names("after") {
        return Err(Fail::value("turn excludes after and through"));
    }
    read.check().map_err(Fail::value)?;
    if body.names("piece") && (turn.is_none() || read.seq.is_none()) {
        return Err(Fail::value("piece requires turn and seq"));
    }
    if let Some(token) = &read.continuation
        && !(1..=CONTINUATION_BYTES).contains(&token.len())
    {
        return Err(Fail::value("continuation must contain 1..4096 bytes"));
    }
    let page_bytes = read.page_bytes.min(cap);
    Ok((conversation_id, RecordRead { page_bytes, ..read }))
}

pub fn read_records(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
    writer: &mut Writer,
) -> Result<(), Fail> {
    kept(store)?;
    let (conversation_id, read) = selection(payload, limits::page_bytes()?)?;
    page(store, conversation_id, &read, writer)
}

/// The page into `writer`: headers there, records beside, then joined.
fn page(
    store: &dyn Backend,
    conversation_id: &str,
    read: &RecordRead,
    writer: &mut Writer,
) -> Result<(), Fail> {
    let mut records = writer.scratch();
    let (mut turns, mut pieces) = (0_u64, 0_u64);
    writer.text("{\"turns\":[")?;
    let next = store.read_records(conversation_id, read, &mut |item| {
        let (spool, count) = match item {
            ReadItem::Header(_) => (&mut *writer, &mut turns),
            ReadItem::Record { .. } => (&mut records, &mut pieces),
        };
        if *count > 0 {
            spool.text(",")?;
        }
        *count += 1;
        Ok(spool.encoded(dumps(&item.to_json(), false))?)
    })?;
    writer.text("],\"records\":[")?;
    writer.join(records)?;
    let token = next.map(|next| next.encode(&read.fingerprint(conversation_id)));
    writer.text("],\"continuation\":")?;
    writer.value(&json!(token))?;
    Ok(writer.text("}")?)
}

#[cfg(test)]
#[path = "record_pages_tests.rs"]
mod tests;
