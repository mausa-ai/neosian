//! `conversation/search_turns` and `conversation/read_turns`, streamed one
//! turn at a time from the store (DESIGN §40.46), and size-paged when the
//! request carries `page_bytes`. Without it each answer's bytes are exactly
//! what they were; with it a page holds the whole turns that fit and a
//! `continuation` names where the next page starts.

use serde_json::{Value, json};

use super::backend::Backend;
use super::codec;
use super::envelope::Fail;
use super::pages::{self, Fill, Paging};
use super::paging::{PAGE, window};
use super::params::{Payload, optional_int, optional_str, optional_strs, require_str};
use super::stream::Writer;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::page::{Last, Resume};
use crate::conversation::rank::{Order, Request};
use crate::conversation::search::parse_query;
use crate::pyfmt::dumps;
use crate::store::cursor::Kind;
use crate::store::time::Stamp;

const SEARCH: &str = "search_turns";
const READ: &str = "read_turns";

fn text(value: &Value) -> String {
    value.as_str().unwrap_or_default().to_string()
}

fn count(value: &Value) -> u64 {
    value.as_u64().unwrap_or_default()
}

fn instant(value: &Value) -> i64 {
    Stamp::parse(value.as_str().unwrap_or_default()).map_or(0, Stamp::instant)
}

pub fn search_turns(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
    writer: &mut Writer,
) -> Result<(), Fail> {
    let limit = optional_int(payload, "limit")?;
    if limit.is_some_and(|limit| limit > PAGE) {
        let why = format!("a search answers at most {PAGE} turns; narrow the query");
        return Err(Fail::value(why));
    }
    let query = require_str(payload, "query")?;
    let ids = optional_strs(payload, "conversations")?;
    let limit = limit.unwrap_or(50);
    let terms = parse_query(&query).map_err(Fail::value)?;
    for conversation_id in ids.iter().flatten() {
        parse_conversation_id(conversation_id)?;
    }
    if limit < 1 {
        return Err(Fail::value(format!("limit must be >= 1, got {limit}")));
    }
    let order = optional_str(payload, "order")?;
    let since = optional_str(payload, "since")?;
    let before = optional_str(payload, "before")?;
    // Version 1 is newest without bounds; history ranking version 1 (§40.43)
    // adds the orders and bounds.
    let chosen = match order.as_deref() {
        None | Some("newest") => Order::Newest,
        Some("relevance") => Order::Relevance,
        Some(other) => {
            let why = format!("order must be newest or relevance, got {other:?}");
            return Err(Fail::value(why));
        }
    };
    let paging = pages::requested(payload)?;
    let selection = json!([SEARCH, terms, ids, order, since, before, limit]);
    let mut request = Request {
        terms,
        ids,
        limit: limit as usize,
        order: chosen,
        since,
        before,
    };
    request.bounds().map_err(Fail::value)?;
    let fingerprint = pages::fingerprint(&selection);
    let (resume, returned) = resume(&mut request, paging.as_ref(), &fingerprint)?;
    if chosen == Order::Newest {
        request.limit -= returned;
    }
    let mut fill = Fill::new(paging.as_ref().map(|paging| paging.ceiling));
    let mut scores = Vec::new();
    let mut last = None;
    writer.text("{\"turns\":[")?;
    let digest = store.rank_page(&request, &resume, &mut |turn, score| {
        let item = dumps(&codec::turn(&turn), false);
        if !fill.admit(item.len()) {
            return Ok(false);
        }
        if fill.count > 1 {
            writer.text(",")?;
        }
        writer.encoded(item)?;
        scores.push(score);
        last = Some((Last::of(&turn), score));
        Ok(true)
    })?;
    writer.text("]")?;
    if order.is_some() {
        writer.text(",\"order\":")?;
        writer.value(&json!(chosen.name()))?;
    }
    if chosen == Order::Relevance {
        writer.text(",\"scores\":")?;
        writer.value(&json!(scores))?;
    }
    if paging.is_some() {
        let token = match last.filter(|_| fill.more) {
            None => None,
            Some((last, score)) => {
                let returned = json!(returned + fill.count);
                let at = json!(Stamp::from_micros(last.created).z());
                let place = [at, json!(last.conversation), json!(last.turn), returned];
                Some(match chosen {
                    Order::Newest => pages::encode(SEARCH, &fingerprint, &place),
                    Order::Relevance => {
                        let mut position = vec![
                            json!(request.before.clone().unwrap_or_default()),
                            json!(digest.unwrap_or_default()),
                            json!(score.unwrap_or_default()),
                        ];
                        position.extend(place);
                        pages::encode(SEARCH, &fingerprint, &position)
                    }
                })
            }
        };
        writer.text(",\"continuation\":")?;
        writer.value(&json!(token))?;
    }
    Ok(writer.text("}")?)
}

/// Where a size-paged search resumes and how many turns earlier pages
/// returned. A first relevance page fixes its evaluation bound: the request's
/// `before`, else the server's clock (unless `since` is not before it).
fn resume(
    request: &mut Request,
    paging: Option<&Paging>,
    fingerprint: &str,
) -> Result<(Resume, usize), Fail> {
    let Some(paging) = paging else {
        return Ok((Resume::Fresh, 0));
    };
    let Some(token) = paging.continuation.as_deref() else {
        if request.order == Order::Relevance && request.before.is_none() {
            let now = Stamp::now();
            let (since, _) = request.bounds().map_err(Fail::value)?;
            if since.is_none_or(|since| since < now.instant()) {
                request.before = Some(now.z());
            }
        }
        return Ok((Resume::Fresh, 0));
    };
    let place = [Kind::Instant, Kind::Str, Kind::Int, Kind::Int];
    let (resume, returned) = if request.order == Order::Newest {
        let parts = pages::decode(SEARCH, token, fingerprint, &place)?;
        let last = Last {
            created: instant(&parts[0]),
            conversation: text(&parts[1]),
            turn: count(&parts[2]),
        };
        (Resume::After(last), count(&parts[3]) as usize)
    } else {
        let mut kinds = vec![Kind::Str, Kind::Str, Kind::Int];
        kinds.extend(place);
        let parts = pages::decode(SEARCH, token, fingerprint, &kinds)?;
        let bound = text(&parts[0]);
        if request
            .before
            .as_ref()
            .is_some_and(|before| *before != bound)
        {
            return Err(pages::malformed(SEARCH, token));
        }
        request.before = (!bound.is_empty()).then_some(bound);
        request
            .bounds()
            .map_err(|_| pages::malformed(SEARCH, token))?;
        let last = Last {
            created: instant(&parts[3]),
            conversation: text(&parts[4]),
            turn: count(&parts[5]),
        };
        let skip = count(&parts[6]) as usize;
        let resume = Resume::Skip {
            skip,
            last,
            score: count(&parts[2]),
            digest: text(&parts[1]),
        };
        (resume, skip)
    };
    if returned < 1 || returned >= request.limit {
        return Err(pages::malformed(SEARCH, token));
    }
    Ok((resume, returned))
}

pub fn read_turns(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
    writer: &mut Writer,
) -> Result<(), Fail> {
    let (conversation_id, after, limit, size) = window(payload)?;
    let Some(paging) = pages::requested(payload)? else {
        // `turns_page` streamed: a total bound answers no continuation.
        let (mut taken, mut last) = (0_i64, None);
        writer.text("{\"turns\":[")?;
        store.read_turns_each(&conversation_id, after, Some(size), &mut |turn| {
            if taken > 0 {
                writer.text(",")?;
            }
            writer.value(&codec::turn(&turn))?;
            taken += 1;
            last = Some(turn.turn);
            Ok(true)
        })?;
        let next = if taken < size || Some(size) == limit {
            None
        } else {
            last
        };
        writer.text("],\"next_after\":")?;
        writer.value(&json!(next))?;
        return Ok(writer.text("}")?);
    };
    let fingerprint = pages::fingerprint(&json!([READ, conversation_id, after, limit]));
    let (mut start, mut returned, mut hint) = (after, 0_i64, None);
    if let Some(token) = paging.continuation.as_deref() {
        let parts = pages::decode(READ, token, &fingerprint, &[Kind::Int; 4])?;
        (start, returned) = (count(&parts[0]) as i64, count(&parts[1]) as i64);
        hint = Some((count(&parts[2]), count(&parts[3])));
        if start < after || returned < 1 || limit.is_some_and(|limit| returned >= limit) {
            return Err(pages::malformed(READ, token));
        }
    }
    // A page also ends at 500 turns: one more is read to know that more follow.
    let remaining = limit.map(|limit| (limit - returned).max(0));
    let ask = remaining.map_or(PAGE + 1, |remaining| remaining.min(PAGE + 1));
    let mut fill = Fill::new(Some(paging.ceiling));
    let mut last = start;
    writer.text("{\"turns\":[")?;
    let next = store.read_turns_page(&conversation_id, start, Some(ask), hint, &mut |turn| {
        if fill.count as i64 == PAGE {
            fill.more = true;
            return Ok(false);
        }
        let item = dumps(&codec::turn(&turn), false);
        if !fill.admit(item.len()) {
            return Ok(false);
        }
        if fill.count > 1 {
            writer.text(",")?;
        }
        writer.encoded(item)?;
        last = turn.turn as i64;
        Ok(true)
    })?;
    let token = fill.more.then(|| {
        let (offset, line) = next.unwrap_or_default();
        let returned = json!(returned + fill.count as i64);
        let position = [json!(last), returned, json!(offset), json!(line)];
        pages::encode(READ, &fingerprint, &position)
    });
    writer.text("],\"next_after\":null,\"continuation\":")?;
    writer.value(&json!(token))?;
    Ok(writer.text("}")?)
}
