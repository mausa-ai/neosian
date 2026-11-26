//! `store/restore_records` (DESIGN §40.54 item 12): one part of a restore,
//! a probe, a commit or a discard. The body is decoded strictly, its
//! headers, records, projections and trail acts included: an unknown key
//! and a mistyped value are refused, so nothing a sender believes stored
//! is left out of the unit digest. `restore_conversation` keeps its own
//! lenient decoders. Every item is stored as sent and the caller's client
//! is never stamped. A whole-store route: a constrained token and a grant
//! that is not the operator's are refused before this handler runs.

use serde_json::Value;

use super::backend::Backend;
use super::envelope::Fail;
use super::params::Payload;
use super::routes_records::enabled;
use super::strict::{Strict, echoed};
use crate::conversation::rows::KINDS;
use crate::conversation::turn_records::{
    Header, PART_ITEMS, Restore, TimeRefusal, TurnRecord, check_id, restore_time,
};
use crate::conversation::{Projection, Redaction};
use crate::repr::repr;

const RESTORE: [&str; 10] = [
    "conversation_id",
    "restore",
    "turns",
    "records",
    "projections_from",
    "projections",
    "redactions_from",
    "redactions",
    "commit",
    "discard",
];
/// What a discard may not name, at any value.
const KEPT_OUT: [&str; 7] = [
    "turns",
    "records",
    "projections_from",
    "projections",
    "redactions_from",
    "redactions",
    "commit",
];
const PROJECTION: [&str; 4] = ["turn", "kind", "text", "span"];
const ACT: [&str; 4] = ["conversation_id", "turns", "actor", "created_at"];

/// One object of a list, read strictly under its own keys; a refusal
/// names the field by its place, as `redactions[0].turns`.
fn entry<'a>(item: &'a Value, place: &str, keys: &[&str]) -> Result<Strict<'a>, Fail> {
    let Some(map) = item.as_object() else {
        return Err(Fail::value(format!("{place} must be an object")));
    };
    Strict::within(map, keys, format!("{place}."))
}

/// One record in restore shape: it names its turn.
fn record(item: &Value) -> Result<(u64, TurnRecord), Fail> {
    match TurnRecord::from_json(item).map_err(Fail::value)? {
        (Some(turn), record) => Ok((turn, record)),
        (None, _) => Err(Fail::value("record field \"turn\" is required")),
    }
}

/// A projection with the rules its legacy decoder applies, `span` 1 when
/// absent.
fn projection(place: &str, item: &Value) -> Result<Projection, Fail> {
    let body = entry(item, place, &PROJECTION)?;
    let span = body.unsigned("span")?.unwrap_or(1);
    let turn = body.count("turn")?;
    let (kind, text) = (body.string("kind")?, body.string("text")?);
    let why = if turn < 1 {
        format!("turn must be >= 1, got {turn}")
    } else if !(1 <= span && span <= turn) {
        format!("span must satisfy 1 <= span <= turn, got span={span} for turn={turn}")
    } else if !KINDS.contains(&kind) {
        let got = repr(&echoed(kind));
        format!("kind must be one of ['digest', 'epoch', 'log'], got {got}")
    } else {
        return Ok(Projection {
            turn,
            kind: kind.to_string(),
            text: text.to_string(),
            span,
        });
    };
    Err(Fail::value(format!("{place}: {why}")))
}

/// A trail act of the conversation being restored; `actor` alone may be
/// null or absent.
fn act(place: &str, item: &Value, conversation_id: &str) -> Result<Redaction, Fail> {
    let body = entry(item, place, &ACT)?;
    let refused = |why: String| Fail::value(format!("{place}: {why}"));
    let named = body.string("conversation_id")?;
    if named != conversation_id {
        let named = echoed(named);
        return Err(refused(format!(
            "a trail act names conversation {named:?}, not this one"
        )));
    }
    let turns: Option<Vec<u64>> = body
        .list("turns")?
        .iter()
        .map(|turn| turn.as_u64().filter(|turn| *turn >= 1))
        .collect();
    let Some(turns) = turns.filter(|turns| !turns.is_empty()) else {
        return Err(refused("turns must name turn numbers from 1".into()));
    };
    let time = body.string("created_at")?;
    let created_at = restore_time(time).map_err(|refusal| {
        let why = match refusal {
            TimeRefusal::Naive => "naive wire timestamp refused",
            TimeRefusal::Malformed => "Invalid isoformat string",
            TimeRefusal::Fraction => "a time has at most six fractional digits",
            TimeRefusal::Range => "a time is within years 0000 to 9999 in UTC",
        };
        refused(format!("{why}: {}", repr(&echoed(time))))
    })?;
    Ok(Redaction {
        conversation_id: named.to_string(),
        turns,
        actor: body.nullable_string("actor")?.map(str::to_string),
        created_at,
    })
}

/// A list of the stream and the index of its first entry: named together
/// or not at all.
fn placed<'a>(body: &Strict<'a>, what: &str) -> Result<(Option<u64>, &'a [Value]), Fail> {
    let from = format!("{what}_from");
    if body.names(&from) != body.names(what) {
        return Err(Fail::value(format!("{what} and {from} come together")));
    }
    let items = body.optional_list(what)?.unwrap_or_default();
    Ok((body.unsigned(&from)?, items))
}

/// The conversation and the request as the stores take it. The item
/// count is checked before any item is decoded.
fn request(payload: &Payload) -> Result<(&str, Restore), Fail> {
    let body = &Strict::new(payload, &RESTORE)?;
    let conversation_id = body.string("conversation_id")?;
    let restore = body.string("restore")?;
    // The stores' own rule, here so it answers before any item is decoded.
    check_id("restore", restore).map_err(Fail::value)?;
    let discard = body.boolean("discard", false)?;
    if discard && KEPT_OUT.iter().any(|name| body.names(name)) {
        return Err(Fail::value("a discard carries no content and no commit"));
    }
    let turns = body.optional_list("turns")?.unwrap_or_default();
    let records = body.optional_list("records")?.unwrap_or_default();
    let (projections_from, projections) = placed(body, "projections")?;
    let (redactions_from, redactions) = placed(body, "redactions")?;
    if turns.len() + records.len() + projections.len() + redactions.len() > PART_ITEMS {
        let why = format!("a part carries at most {PART_ITEMS} items");
        return Err(Fail::value(why));
    }
    let header = |item| Header::from_json(item).map_err(Fail::value);
    let entries = projections.iter().enumerate();
    let entries = entries.map(|(at, item)| projection(&format!("projections[{at}]"), item));
    let acts = redactions.iter().enumerate();
    let acts = acts.map(|(at, item)| act(&format!("redactions[{at}]"), item, conversation_id));
    let restore = Restore {
        restore: restore.to_string(),
        turns: turns.iter().map(header).collect::<Result<_, _>>()?,
        records: records.iter().map(record).collect::<Result<_, _>>()?,
        projections_from,
        projections: entries.collect::<Result<_, _>>()?,
        redactions_from,
        redactions: acts.collect::<Result<_, _>>()?,
        commit: body.boolean("commit", false)?,
        discard,
    };
    Ok((conversation_id, restore))
}

pub fn restore_records(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
) -> Result<Value, Fail> {
    let limits = enabled(store)?;
    let (conversation_id, restore) = request(payload)?;
    let restored = store.restore_records(conversation_id, restore, &limits)?;
    Ok(restored.to_json())
}

#[cfg(test)]
#[path = "record_restore_tests.rs"]
mod tests;
