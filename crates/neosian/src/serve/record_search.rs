//! `conversation/search_records` and `store/sweep_records` (DESIGN §40.54
//! items 11 and 13). Both bodies are decoded strictly and both answers
//! write their keys in their table's order. A search is authorized from
//! its `conversations` before this handler runs, so nothing is scanned for
//! a client that may not read; the sweep is a whole-store route, refused
//! to a constrained token and to a grant that is not the operator's.

use serde_json::{Map, Value, json};

use super::backend::Backend;
use super::envelope::Fail;
use super::params::Payload;
use super::record_kinds::kinds;
use super::routes_records::kept;
use super::strict::Strict;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::RecordSearch;

const SEARCH: [&str; 7] = [
    "query",
    "conversations",
    "kinds",
    "order",
    "since",
    "before",
    "limit",
];
const SWEEP: [&str; 3] = ["limit", "cursor", "write"];
/// Hits answered when a search names no `limit`.
const HITS: u64 = 20;
/// Conversations visited when a sweep names no `limit`.
const VISITED: u64 = 50;

/// `conversations`: ids as the store checks them; none when absent.
fn conversations(body: &Strict) -> Result<Option<Vec<String>>, Fail> {
    let Some(items) = body.optional_list("conversations")? else {
        return Ok(None);
    };
    let id = |item: &Value| {
        let text = item.as_str().map(str::to_string);
        text.ok_or_else(|| Fail::value("parameter 'conversations' must be a list of ids"))
    };
    items.iter().map(id).collect::<Result<_, _>>().map(Some)
}

/// `order`: the order and whether the request named one, which the answer
/// then echoes.
fn order(body: &Strict) -> Result<(Order, bool), Fail> {
    match body.optional_string("order")? {
        None => Ok((Order::Newest, false)),
        Some("newest") => Ok((Order::Newest, true)),
        Some("relevance") => Ok((Order::Relevance, true)),
        Some(_) => Err(Fail::value("order must be newest or relevance")),
    }
}

/// A count as the stores take it; one past any bound stays past it.
fn count(body: &Strict, name: &str, default: u64) -> Result<usize, Fail> {
    let named = body.unsigned(name)?.unwrap_or(default);
    Ok(usize::try_from(named).unwrap_or(usize::MAX))
}

/// The request as the stores take it, and whether it named an order.
fn search(payload: &Payload) -> Result<(RecordSearch, bool), Fail> {
    let body = Strict::new(payload, &SEARCH)?;
    let query = body.string("query")?;
    let (order, named) = order(&body)?;
    let instant = |name| Ok::<_, Fail>(body.optional_string(name)?.map(str::to_string));
    let search = RecordSearch::new(
        query,
        kinds(&body, "kinds")?,
        order,
        instant("since")?,
        instant("before")?,
        count(&body, "limit", HITS)?,
    )
    .map_err(Fail::value)?;
    let conversations = conversations(&body)?;
    Ok((
        RecordSearch {
            conversations,
            ..search
        },
        named,
    ))
}

pub fn search_records(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
) -> Result<Value, Fail> {
    kept(store)?;
    let (search, named) = search(payload)?;
    let hits = store.search_records(&search)?;
    let mut answer = Map::new();
    let hits = hits.iter().map(|hit| hit.to_json()).collect();
    answer.insert("hits".into(), Value::Array(hits));
    if named {
        answer.insert("order".into(), json!(search.order.name()));
    }
    Ok(Value::Object(answer))
}

/// The sweep's `limit`, `cursor` and `write`; the store checks their
/// ranges before it visits anything.
fn sweep(payload: &Payload) -> Result<(usize, Option<&str>, bool), Fail> {
    let body = Strict::new(payload, &SWEEP)?;
    Ok((
        count(&body, "limit", VISITED)?,
        body.optional_string("cursor")?,
        body.boolean("write", false)?,
    ))
}

pub fn sweep_records(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    kept(store)?;
    let (limit, cursor, write) = sweep(payload)?;
    let page = store.sweep_records(limit, cursor, write)?;
    let mut answer = Map::new();
    let swept = page.conversations.iter().map(|one| {
        json!({
            "conversation_id": one.conversation_id,
            "drafts": one.drafts,
            "restores": one.restores,
            "freed_bytes": one.freed_bytes,
        })
    });
    answer.insert("conversations".into(), swept.collect());
    answer.insert("next_cursor".into(), json!(page.next_cursor));
    answer.insert("write".into(), json!(page.write));
    Ok(Value::Object(answer))
}

#[cfg(test)]
#[path = "record_search_tests.rs"]
mod tests;
