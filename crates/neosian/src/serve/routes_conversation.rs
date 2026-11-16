//! The eight `conversation/*` routes (`server/routes.py`,
//! `erasure_routes.py`, DESIGN §18.2, §32, §38): the `ConversationStore`
//! methods and the eraser's two by name; a search and a trail read are
//! bounded, never a page.

use serde_json::{Value, json};

use super::backend::Backend;
use super::codec;
use super::envelope::Fail;
use super::paging::{PAGE, projections_page};
use super::params::{
    Payload, optional_int, optional_ints, optional_strs, optional_timestamp, require_objects,
    require_str,
};
use super::routes::{Handler, Route};
use super::routes_memory::actor;
use super::stream::Writer;
use super::turn_pages::{read_turns, search_turns};

pub const ROUTES: [Route; 8] = [
    Route {
        name: "conversation/append_turn",
        handler: Handler::Whole(append_turn),
        whole_store: false,
    },
    Route {
        name: "conversation/read_turns",
        handler: Handler::Streamed(read_turns),
        whole_store: false,
    },
    Route {
        name: "conversation/last_turn_number",
        handler: Handler::Whole(last_turn_number),
        whole_store: false,
    },
    Route {
        name: "conversation/search_turns",
        handler: Handler::Streamed(search_turns),
        whole_store: false,
    },
    Route {
        name: "conversation/append_projections",
        handler: Handler::Whole(append_projections),
        whole_store: false,
    },
    Route {
        name: "conversation/read_projections",
        handler: Handler::Streamed(read_projections),
        whole_store: false,
    },
    Route {
        name: "conversation/redact_turns",
        handler: Handler::Whole(redact_turns),
        whole_store: false,
    },
    Route {
        name: "conversation/turn_redactions",
        handler: Handler::Whole(turn_redactions),
        whole_store: false,
    },
];

fn append_turn(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let conversation_id = require_str(payload, "conversation_id")?;
    let messages = require_objects(payload, "messages")?
        .into_iter()
        .map(codec::decode_message)
        .collect::<Result<Vec<_>, Fail>>()?;
    let actor = actor(client, payload)?;
    let turn = store.append_turn(&conversation_id, messages, Some(actor.as_str().to_string()))?;
    Ok(json!({"turn": codec::turn(&turn)}))
}

fn last_turn_number(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let conversation_id = require_str(payload, "conversation_id")?;
    Ok(json!({"turn": store.last_turn_number(&conversation_id)?}))
}

fn append_projections(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
) -> Result<Value, Fail> {
    let conversation_id = require_str(payload, "conversation_id")?;
    let entries = require_objects(payload, "entries")?
        .into_iter()
        .map(|entry| codec::decode_projection(entry.as_object().expect("an object")))
        .collect::<Result<Vec<_>, Fail>>()?;
    store.append_projections(&conversation_id, &entries)?;
    Ok(json!({}))
}

fn read_projections(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
    writer: &mut Writer,
) -> Result<(), Fail> {
    let (entries, next_after) = projections_page(store, payload)?;
    let items = entries.iter().map(codec::projection);
    Ok(writer.list("entries", items, &[("next_after", json!(next_after))])?)
}

fn redact_turns(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let conversation_id = require_str(payload, "conversation_id")?;
    let through = optional_int(payload, "through")?;
    let turns = optional_ints(payload, "turns")?;
    let actor = actor(client, payload)?;
    let count = store.redact_turns(
        &conversation_id,
        through,
        turns.as_deref(),
        Some(actor.as_str()),
    )?;
    Ok(json!({"count": count}))
}

fn turn_redactions(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let limit = optional_int(payload, "limit")?;
    if limit.is_some_and(|limit| limit > PAGE) {
        let why = format!("the trail answers at most {PAGE} acts; narrow the window");
        return Err(Fail::value(why));
    }
    let ids = optional_strs(payload, "conversations")?;
    let since = optional_timestamp(payload, "since")?;
    let acts = store.turn_redactions(ids.as_deref(), since, limit.unwrap_or(50))?;
    let acts: Vec<Value> = acts.iter().map(codec::turn_redaction).collect();
    Ok(json!({"redactions": acts}))
}
