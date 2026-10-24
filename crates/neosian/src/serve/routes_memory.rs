//! The nine `memory/*` routes (`server/routes.py`, DESIGN §18.2): the
//! `MemoryStore` methods by name, a write stamped under the token's
//! client, every listing a page.

use serde_json::{Value, json};

use super::auth::stamp;
use super::backend::Backend;
use super::codec;
use super::envelope::Fail;
use super::paging::memory_page;
use super::params::{Payload, optional_int, optional_str, optional_timestamp, require_str};
use super::routes::Route;
use crate::store::grammar::Actor;

pub const ROUTES: [Route; 9] = [
    Route {
        name: "memory/read",
        handler: read,
        whole_store: false,
    },
    Route {
        name: "memory/write",
        handler: write,
        whole_store: false,
    },
    Route {
        name: "memory/delete",
        handler: delete,
        whole_store: false,
    },
    Route {
        name: "memory/rename",
        handler: rename,
        whole_store: false,
    },
    Route {
        name: "memory/list_documents",
        handler: list_documents,
        whole_store: false,
    },
    Route {
        name: "memory/versions",
        handler: versions,
        whole_store: false,
    },
    Route {
        name: "memory/redact",
        handler: redact,
        whole_store: false,
    },
    Route {
        name: "memory/history",
        handler: history,
        whole_store: false,
    },
    Route {
        name: "memory/redactions",
        handler: redactions,
        whole_store: false,
    },
];

/// `stamp(client, optional_str(payload, "actor"))` as the store's actor.
pub fn actor(client: &str, payload: &Payload) -> Result<Actor, Fail> {
    let body = optional_str(payload, "actor")?;
    Ok(Actor::stamped(&stamp(client, body.as_deref())))
}

fn read(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let path = require_str(payload, "path")?;
    let document = store
        .read(&scope, &path)?
        .map(|document| codec::document(&scope, &path, &document))
        .transpose()?;
    Ok(json!({"document": document}))
}

fn write(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let path = require_str(payload, "path")?;
    let content = require_str(payload, "content")?;
    let actor = actor(client, payload)?;
    let expected = optional_int(payload, "expected_version")?;
    let expected = expected.map(|version| u64::try_from(version).unwrap_or(u64::MAX));
    // A legacy block-YAML envelope must be converted before a wire mutation.
    // Refuse before changing its body, rather than failing to encode the receipt.
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

fn versions(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let path = require_str(payload, "path")?;
    let page = memory_page(
        payload,
        Some(50),
        |limit| Ok(store.versions(&scope, &path, limit as usize)?),
        |cursor, size| Ok(store.versions_page(&scope, &path, cursor, size)?),
    )?;
    let versions: Vec<Value> = page.items.iter().map(codec::version).collect();
    Ok(json!({"versions": versions, "next_cursor": page.next_cursor}))
}

fn redact(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let path = optional_str(payload, "path")?;
    let actor = actor(client, payload)?;
    Ok(json!({"count": store.redact(&scope, path.as_deref(), &actor)?}))
}

fn history(store: &dyn Backend, payload: &Payload, _client: &str) -> Result<Value, Fail> {
    let scope = require_str(payload, "scope")?;
    let since = optional_timestamp(payload, "since")?;
    let page = memory_page(
        payload,
        None,
        |_| Ok(store.history(&scope, since, None)?),
        |cursor, size| Ok(store.history_page(&scope, since, cursor, size)?),
    )?;
    let versions: Vec<Value> = page.items.iter().map(codec::version).collect();
    Ok(json!({"versions": versions, "next_cursor": page.next_cursor}))
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
