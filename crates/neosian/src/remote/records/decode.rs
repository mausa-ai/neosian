//! The five answers, read by key into the types the stores answer with. A
//! key the route promises and does not carry, or carries mistyped, makes
//! the answer unreadable. A header and a record are read by the strict
//! decoders the server itself uses, which refuse an unknown key: every
//! answer schema of version 1 is closed, so a new key needs a new version
//! and a client that met one here would not know what it changes. Lists
//! are taken out of the answer, so an item is not held twice once it is
//! decoded.

use serde_json::Value;

use std::cmp::Reverse;

use super::{Answered, Failure, RecordPage, RecordQuery};
use crate::conversation::rank::{Order, bounds, within};
use crate::conversation::turn_records::sweep::{SweepConversation, SweepPage};
use crate::conversation::turn_records::{
    Header, Kind, Position, RecordHit, Restored, SEARCHABLE, Staged, State, TurnRecord, Written,
};
use crate::store::time::Stamp;

type Decoded<T> = Result<T, Failure>;

fn bad(what: &str) -> Failure {
    Failure::Unreadable(what.to_string())
}
fn field<'a>(data: &'a Value, key: &str) -> Decoded<&'a Value> {
    data.get(key).ok_or_else(|| bad(key))
}
fn number(data: &Value, key: &str) -> Decoded<u64> {
    field(data, key)?.as_u64().ok_or_else(|| bad(key))
}
fn count(data: &Value, key: &str) -> Decoded<usize> {
    usize::try_from(number(data, key)?).map_err(|_| bad(key))
}
fn string(data: &Value, key: &str) -> Decoded<String> {
    let found = field(data, key)?.as_str();
    found.map(str::to_string).ok_or_else(|| bad(key))
}
/// A key that is always answered, as a string or null.
fn nullable(data: &Value, key: &str) -> Decoded<Option<String>> {
    match field(data, key)? {
        Value::Null => Ok(None),
        Value::String(text) => Ok(Some(text.clone())),
        _ => Err(bad(key)),
    }
}
fn list(data: &mut Value, key: &str) -> Decoded<Vec<Value>> {
    match data.get_mut(key).map(Value::take) {
        Some(Value::Array(items)) => Ok(items),
        _ => Err(bad(key)),
    }
}
fn header(value: &Value) -> Decoded<Header> {
    Header::from_json(value).map_err(Failure::Unreadable)
}

pub fn written(data: Value) -> Decoded<Written> {
    let state = match string(&data, "state")?.as_str() {
        "none" => State::None,
        "open" => State::Open,
        "sealed" => State::Sealed,
        "discarded" => State::Discarded,
        _ => return Err(bad("state")),
    };
    let turn = match field(&data, "turn")? {
        Value::Null => None,
        sealed => Some(header(sealed)?),
    };
    Ok(Written {
        draft: string(&data, "draft")?,
        state,
        next_seq: number(&data, "next_seq")?,
        turn,
        stored_bytes: number(&data, "stored_bytes")?,
        freed_bytes: number(&data, "freed_bytes")?,
    })
}

pub fn restored(data: Value) -> Decoded<Restored> {
    let state = match string(&data, "state")?.as_str() {
        "none" => Staged::None,
        "open" => Staged::Open,
        "committed" => Staged::Committed,
        "discarded" => Staged::Discarded,
        _ => return Err(bad("state")),
    };
    let position = match field(&data, "position")? {
        Value::Null => None,
        at => Some(Position {
            turn: number(at, "turn")?,
            seq: number(at, "seq")?,
            complete: field(at, "complete")?
                .as_bool()
                .ok_or_else(|| bad("complete"))?,
            projections: number(at, "projections")?,
            redactions: number(at, "redactions")?,
        }),
    };
    Ok(Restored {
        restore: string(&data, "restore")?,
        state,
        position,
        stored_bytes: number(&data, "stored_bytes")?,
        freed_bytes: number(&data, "freed_bytes")?,
        digest: nullable(&data, "digest")?,
    })
}

/// An answered record: a record in restore shape with `tail` and
/// `truncated` behind it.
fn answered(item: Value) -> Decoded<Answered> {
    let Value::Object(mut map) = item else {
        return Err(bad("records"));
    };
    let tail = match map.remove("tail") {
        Some(Value::Null) => None,
        Some(Value::String(tail)) => Some(tail),
        _ => return Err(bad("tail")),
    };
    let Some(Value::Bool(truncated)) = map.remove("truncated") else {
        return Err(bad("truncated"));
    };
    match TurnRecord::from_json(&Value::Object(map)).map_err(Failure::Unreadable)? {
        (Some(turn), record) => Ok(Answered {
            turn,
            record,
            tail,
            truncated,
        }),
        (None, _) => Err(bad("turn")),
    }
}

pub fn page(mut data: Value) -> Decoded<RecordPage> {
    let turns = list(&mut data, "turns")?;
    let records = list(&mut data, "records")?;
    Ok(RecordPage {
        turns: turns.iter().map(header).collect::<Decoded<_>>()?,
        records: records.into_iter().map(answered).collect::<Decoded<_>>()?,
        continuation: nullable(&data, "continuation")?,
    })
}

fn hit(item: &Value) -> Decoded<RecordHit> {
    let excerpt = field(item, "excerpt")?;
    let score = match item.get("score") {
        None => None,
        Some(score) => Some(score.as_u64().ok_or_else(|| bad("score"))?),
    };
    Ok(RecordHit {
        conversation_id: string(item, "conversation_id")?,
        turn: number(item, "turn")?,
        seq: number(item, "seq")?,
        message: number(item, "message")?,
        kind: Kind::parse(&string(item, "kind")?).ok_or_else(|| bad("kind"))?,
        call_id: nullable(item, "call_id")?,
        name: nullable(item, "name")?,
        created_at: Stamp::parse(&string(item, "created_at")?).map_err(|_| bad("created_at"))?,
        actor: nullable(item, "actor")?,
        size: number(item, "size")?,
        excerpt: (count(excerpt, "offset")?, string(excerpt, "text")?),
        score,
    })
}

/// Hits answered when a search names no `limit`: the route's default.
const HITS: usize = 20;
/// The most Unicode scalars an excerpt holds.
const EXCERPT: usize = 240;

/// The hits of a search, checked against the request as the ranked
/// history search checks its turns: the echoed order (named when the
/// request named one, absent otherwise), at most `limit` hits, each inside
/// the conversations, kinds and time bounds asked, a score under relevance
/// alone, an excerpt of at most 240 scalars, and the order asked for with
/// no hit twice. Bounds this client
/// cannot read are the server's to refuse and are not compared.
pub fn hits(mut data: Value, query: &RecordQuery) -> Decoded<Vec<RecordHit>> {
    let echoed = data.get("order").and_then(Value::as_str);
    if echoed != query.order.map(Order::name) {
        return Err(bad("order"));
    }
    let hits: Vec<RecordHit> = list(&mut data, "hits")?
        .iter()
        .map(hit)
        .collect::<Decoded<_>>()?;
    if hits.len() > query.limit.unwrap_or(HITS) {
        return Err(bad("more hits than the limit"));
    }
    let relevance = query.order == Some(Order::Relevance);
    let times = bounds(query.since.as_deref(), query.before.as_deref()).unwrap_or((None, None));
    let kinds = query.kinds.as_deref().unwrap_or(&SEARCHABLE);
    let mut previous = None;
    for hit in &hits {
        let named = query.conversations.as_ref();
        if named.is_some_and(|ids| !ids.contains(&hit.conversation_id)) {
            return Err(bad("a hit outside the conversations asked"));
        }
        if !kinds.contains(&hit.kind) {
            return Err(bad("a hit outside the kinds asked"));
        }
        if !within(times, hit.created_at.instant()) {
            return Err(bad("a hit outside the time bounds asked"));
        }
        if hit.score.is_some() != relevance {
            return Err(bad("score"));
        }
        if hit.excerpt.1.chars().count() > EXCERPT {
            return Err(bad("an excerpt longer than 240 scalars"));
        }
        // Newest: time, conversation and turn descending, then `seq`
        // ascending; relevance puts the score before them, descending.
        let newest = Reverse((hit.created_at.instant(), &hit.conversation_id, hit.turn));
        let place = (Reverse(hit.score), newest, hit.seq);
        if previous.as_ref().is_some_and(|previous| *previous >= place) {
            return Err(bad("hits out of order"));
        }
        previous = Some(place);
    }
    Ok(hits)
}

pub fn swept(mut data: Value) -> Decoded<SweepPage> {
    let one = |item: &Value| {
        Ok(SweepConversation {
            conversation_id: string(item, "conversation_id")?,
            drafts: count(item, "drafts")?,
            restores: count(item, "restores")?,
            freed_bytes: number(item, "freed_bytes")?,
        })
    };
    let conversations = list(&mut data, "conversations")?;
    Ok(SweepPage {
        conversations: conversations.iter().map(one).collect::<Decoded<_>>()?,
        next_cursor: nullable(&data, "next_cursor")?,
        write: field(&data, "write")?
            .as_bool()
            .ok_or_else(|| bad("write"))?,
    })
}
