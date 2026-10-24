//! The four `store/*` routes (`server/portable_routes.py`, DESIGN §26.4):
//! the `Portable` protocol over the wire. A restore is verbatim, the
//! archive's own actors and never the caller's stamp; the four refuse a
//! constrained client outright.

use serde_json::{Value, json};

use super::backend::Backend;
use super::codec;
use super::envelope::Fail;
use super::params::{Payload, optional_objects, require_objects, require_str};
use super::routes::Route;

pub const ROUTES: [Route; 4] = [
    Route {
        name: "store/scopes",
        handler: scopes,
        whole_store: true,
    },
    Route {
        name: "store/conversations",
        handler: conversations,
        whole_store: true,
    },
    Route {
        name: "store/restore_scope",
        handler: restore_scope,
        whole_store: true,
    },
    Route {
        name: "store/restore_conversation",
        handler: restore_conversation,
        whole_store: true,
    },
];

/// Every object of a required list through `decode`.
fn each<T>(
    payload: &Payload,
    name: &str,
    required: bool,
    decode: fn(&Payload) -> Result<T, Fail>,
) -> Result<Vec<T>, Fail> {
    let items = if required {
        require_objects(payload, name)?
    } else {
        optional_objects(payload, name)?
    };
    items
        .into_iter()
        .map(|item| decode(item.as_object().expect("an object")))
        .collect()
}

fn scopes(store: &dyn Backend, _payload: &Payload, _client: &str) -> Result<Value, Fail> {
    Ok(json!({"scopes": store.scopes()?}))
}

fn conversations(store: &dyn Backend, _payload: &Payload, _client: &str) -> Result<Value, Fail> {
    Ok(json!({"conversations": store.conversations()?}))
}

fn restore_scope(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let documents = each(payload, "documents", true, codec::decode_document)?;
    let versions = each(payload, "versions", true, codec::decode_version)?;
    let redactions = each(payload, "redactions", true, codec::decode_redaction)?;
    store.restore_scope(&scope, &documents, versions, &redactions)?;
    Ok(json!({}))
}

fn restore_conversation(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
) -> Result<Value, Fail> {
    let conversation_id = require_str(payload, "conversation_id")?;
    let turns = each(payload, "turns", true, codec::decode_turn)?;
    let projections = each(payload, "projections", true, codec::decode_projection)?;
    let redactions = each(payload, "redactions", false, codec::decode_turn_redaction)?;
    store.restore_conversation(&conversation_id, &turns, &projections, &redactions)?;
    Ok(json!({}))
}
