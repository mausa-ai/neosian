//! Read-only operator information, bounded and authorized by mounts.

use serde_json::{Map, Value, json};

use super::{Outcome, Tool, integer, text};
use crate::assets::{mapping, quoted};
use crate::docs::{list_topics, load_page};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::memory::mounts::resolve;
use crate::store::grammar::validate_document_path;

const ASSET: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/core-tools.yaml"));
const DOC_FIELDS: [Field; 1] = [Field {
    name: "topic",
    kind: Kind::Str,
    presence: Presence::Optional,
}];
const HISTORY_FIELDS: [Field; 2] = [
    Field {
        name: "path",
        kind: Kind::Str,
        presence: Presence::Required,
    },
    Field {
        name: "limit",
        kind: Kind::Int,
        presence: Presence::Default("20"),
    },
];

pub fn docs() -> Tool {
    Tool {
        name: "docs",
        description: quoted(ASSET, "docs").expect("docs description"),
        params: mapping(ASSET, "docs_params").expect("docs params"),
        fields: &DOC_FIELDS,
        read_only: true,
        destructive: false,
        call: docs_call,
    }
}

pub fn history() -> Tool {
    Tool {
        name: "memory_history",
        description: quoted(ASSET, "memory_history").expect("history description"),
        params: mapping(ASSET, "memory_history_params").expect("history params"),
        fields: &HISTORY_FIELDS,
        read_only: true,
        destructive: false,
        call: history_call,
    }
}

fn docs_call(_server: &Server, args: &Map<String, Value>) -> Outcome {
    match text(args, "topic").filter(|topic| !topic.is_empty()) {
        Some(topic) => match load_page(topic) {
            Some(page) => Outcome::ok(page.body),
            None => Outcome::fail("Unknown documentation topic")
                .hint("Call docs without a topic for the listing."),
        },
        None => Outcome::ok(
            json!(list_topics().into_iter().map(|page|
            json!({"topic": page.topic, "title": page.title, "summary": page.summary})
        ).collect::<Vec<_>>())
            .to_string(),
        ),
    }
}

fn history_call(server: &Server, args: &Map<String, Value>) -> Outcome {
    let limit = integer(args, "limit").unwrap_or(20);
    if !(1..=100).contains(&limit) {
        return Outcome::fail("limit must be between 1 and 100");
    }
    let path = text(args, "path").unwrap_or_default();
    let (mount, document) = match resolve(&server.memory.mounts, path) {
        Ok(resolved) => resolved,
        Err(error) => return Outcome::fail(error.message),
    };
    if !document.is_empty()
        && let Err(error) = validate_document_path(document)
    {
        return Outcome::fail(error.message);
    }
    let store = &server.memory.store;
    let rows = if document.is_empty() {
        store.history(&mount.scope, None, Some(limit as usize))
    } else {
        store.versions(&mount.scope, document, limit as usize)
    };
    match rows {
        Err(error) => Outcome::failed(
            "memory_history",
            &crate::conversation::Error::from(error).text(),
        ),
        Ok(rows) => Outcome::ok(
            json!({"path": path, "entries": rows.iter().map(|row| json!({
            "path": format!("/{}/{}", mount.path, row.path), "version": row.version,
            "action": row.action.as_str(), "actor": row.actor, "created_at": row.created_at.iso(),
            "redacted": row.redacted
        })).collect::<Vec<_>>()} )
            .to_string(),
        ),
    }
}
