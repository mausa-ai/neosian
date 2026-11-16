//! The base `memory/*` routes plus optional current-document search: the
//! `MemoryStore` methods by name, a write stamped under the token's
//! client, every listing a page.

use serde_json::{Value, json};

use super::auth::stamp;
use super::backend::Backend;
use super::codec;
use super::envelope::Fail;
use super::paging::memory_page;
use super::params::{Payload, optional_int, optional_str, optional_timestamp, require_str};
use super::routes::{Handler, Route};
use super::stream::Writer;
use crate::store::grammar::Actor;

pub const ROUTES: [Route; 12] = [
    Route {
        name: "memory/changes",
        handler: Handler::Whole(changes),
        whole_store: false,
    },
    Route {
        name: "memory/prune_changes",
        handler: Handler::Whole(prune_changes),
        whole_store: false,
    },
    Route {
        name: "memory/search",
        handler: Handler::Whole(search),
        whole_store: false,
    },
    Route {
        name: "memory/read",
        handler: Handler::Streamed(read),
        whole_store: false,
    },
    Route {
        name: "memory/write",
        handler: Handler::Whole(write),
        whole_store: false,
    },
    Route {
        name: "memory/delete",
        handler: Handler::Whole(delete),
        whole_store: false,
    },
    Route {
        name: "memory/rename",
        handler: Handler::Whole(rename),
        whole_store: false,
    },
    Route {
        name: "memory/list_documents",
        handler: Handler::Whole(list_documents),
        whole_store: false,
    },
    Route {
        name: "memory/versions",
        handler: Handler::Streamed(versions),
        whole_store: false,
    },
    Route {
        name: "memory/redact",
        handler: Handler::Whole(redact),
        whole_store: false,
    },
    Route {
        name: "memory/history",
        handler: Handler::Streamed(history),
        whole_store: false,
    },
    Route {
        name: "memory/redactions",
        handler: Handler::Whole(redactions),
        whole_store: false,
    },
];

fn changes(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let mut params = payload.clone();
    params.remove("scope");
    let request = serde_json::from_value(Value::Object(params))
        .map_err(|_| Fail::value("invalid change-feed request"))?;
    Ok(
        serde_json::to_value(store.read_changes(&scope, &request)?)
            .expect("change page serializes"),
    )
}

fn prune_changes(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    #[derive(serde::Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Prune {
        scope: String,
        through: String,
        #[serde(default)]
        write: bool,
    }
    let p: Prune = serde_json::from_value(Value::Object(payload.clone()))
        .map_err(|_| Fail::value("invalid change-retention request"))?;
    Ok(
        serde_json::to_value(store.prune_changes(&p.scope, &p.through, p.write)?)
            .expect("prune serializes"),
    )
}

fn search(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let mut parameters = payload.clone();
    parameters.remove("scope");
    let request: crate::memory::search::Request = serde_json::from_value(Value::Object(parameters))
        .map_err(|error| Fail::value(format!("invalid memory-search request: {error}")))?;
    let page = store.search_memory(&scope, &request)?;
    Ok(serde_json::to_value(page).expect("search page serializes"))
}

/// `stamp(client, optional_str(payload, "actor"))` as the store's actor.
pub fn actor(client: &str, payload: &Payload) -> Result<Actor, Fail> {
    let body = optional_str(payload, "actor")?;
    Ok(Actor::stamped(&stamp(client, body.as_deref())))
}

/// One document, streamed: the store's copy goes before it is encoded, so
/// the route holds about one document at a time (DESIGN §40.46).
fn read(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
    writer: &mut Writer,
) -> Result<(), Fail> {
    let scope = require_str(payload, "scope")?;
    let path = require_str(payload, "path")?;
    let document = store
        .read(&scope, &path)?
        .map(|document| codec::document(&scope, &path, &document))
        .transpose()?;
    writer.text("{\"document\":")?;
    writer.value(&document.unwrap_or(Value::Null))?;
    Ok(writer.text("}")?)
}

fn write(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let path = require_str(payload, "path")?;
    let content = require_str(payload, "content")?;
    let actor = actor(client, payload)?;
    let expected = optional_int(payload, "expected_version")?;
    let expected = expected.map(|version| u64::try_from(version).unwrap_or(u64::MAX));
    // Preflight response metadata so unsupported YAML is refused before a write.
    if let Some(existing) = store.read(&scope, &path)? {
        codec::document(&scope, &path, &existing)?;
    }
    let document = store.write_document(&scope, &path, &content, &actor, expected)?;
    Ok(json!({"document": codec::document(&scope, &path, &document)?}))
}

fn delete(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let path = require_str(payload, "path")?;
    let actor = actor(client, payload)?;
    Ok(json!({"deleted": store.delete(&scope, &path, &actor)?.removed}))
}

fn rename(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let src = require_str(payload, "src")?;
    let dst = require_str(payload, "dst")?;
    let actor = actor(client, payload)?;
    if let Some(existing) = store.read(&scope, &src)? {
        codec::document(&scope, &src, &existing)?;
    }
    let document = store.rename_document(&scope, &src, &dst, &actor)?;
    Ok(json!({"document": codec::document(&scope, &dst, &document)?}))
}

fn list_documents(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let prefix = optional_str(payload, "prefix")?.unwrap_or_default();
    let page = memory_page(
        payload,
        None,
        |_| Ok(store.list_documents(&scope, &prefix)?),
        |cursor, size| Ok(store.list_documents_page(&scope, &prefix, cursor, size)?),
    )?;
    let entries: Vec<Value> = page.items.iter().map(codec::entry).collect();
    Ok(json!({"entries": entries, "next_cursor": page.next_cursor}))
}

fn versions(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
    writer: &mut Writer,
) -> Result<(), Fail> {
    let scope = require_str(payload, "scope")?;
    let path = require_str(payload, "path")?;
    let page = memory_page(
        payload,
        Some(50),
        |limit| Ok(store.versions(&scope, &path, limit as usize)?),
        |cursor, size| Ok(store.versions_page(&scope, &path, cursor, size)?),
    )?;
    let items = page.items.iter().map(codec::version);
    Ok(writer.list(
        "versions",
        items,
        &[("next_cursor", json!(page.next_cursor))],
    )?)
}

fn redact(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let path = optional_str(payload, "path")?;
    let actor = actor(client, payload)?;
    Ok(json!({"count": store.redact(&scope, path.as_deref(), &actor)?}))
}

fn history(
    store: &dyn Backend,
    payload: &Payload,
    _client: &str,
    writer: &mut Writer,
) -> Result<(), Fail> {
    let scope = require_str(payload, "scope")?;
    let since = optional_timestamp(payload, "since")?;
    let page = memory_page(
        payload,
        None,
        |_| Ok(store.history(&scope, since, None)?),
        |cursor, size| Ok(store.history_page(&scope, since, cursor, size)?),
    )?;
    let items = page.items.iter().map(codec::version);
    Ok(writer.list(
        "versions",
        items,
        &[("next_cursor", json!(page.next_cursor))],
    )?)
}

fn redactions(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let since = optional_timestamp(payload, "since")?;
    let page = memory_page(
        payload,
        None,
        |_| Ok(store.redactions(&scope, since, None)?),
        |cursor, size| Ok(store.redactions_page(&scope, since, cursor, size)?),
    )?;
    let acts: Vec<Value> = page.items.iter().map(codec::redaction).collect();
    Ok(json!({"redactions": acts, "next_cursor": page.next_cursor}))
}
