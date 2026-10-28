//! The `memory` tool (`memory/tools.py`, `memory/dispatch.py`): one tool
//! with a `command` enum, every call through the ladder the shell shares,
//! the `file_text` alias and the per-command argument checks worded as
//! the Python side words them.

use serde_json::{Map, Value};

use super::{Outcome, Tool, integer, text};
use crate::assets::{MEMORY, folded, mapping};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::memory::dispatch::{Call, corrective, dispatch};
use crate::memory::mounts::{resolve, state_path};
use crate::pyfmt::py_str;
use crate::repr::repr;

const COMMANDS: [&str; 6] = [
    "view",
    "create",
    "str_replace",
    "insert",
    "delete",
    "rename",
];
const MAILBOX: &str =
    "Use messages for mailbox lifecycle changes; operator redaction remains available.";

const FIELDS: [Field; 11] = [
    Field {
        name: "command",
        kind: Kind::Literal(&COMMANDS),
        presence: Presence::Required,
    },
    Field {
        name: "path",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "view_range",
        kind: Kind::IntList,
        presence: Presence::Optional,
    },
    Field {
        name: "content",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "file_text",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "old_str",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "new_str",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "insert_line",
        kind: Kind::Int,
        presence: Presence::Optional,
    },
    Field {
        name: "insert_text",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "old_path",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "new_path",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
];

pub fn tool() -> Tool {
    Tool {
        name: "memory",
        description: folded(MEMORY, "tool").expect("memory.yaml carries the tool"),
        params: mapping(MEMORY, "params").expect("memory.yaml carries the params"),
        fields: &FIELDS,
        read_only: false,
        destructive: true,
        call,
    }
}

fn required<'a>(value: Option<&'a str>, command: &str, name: &str) -> Result<&'a str, Outcome> {
    value.ok_or_else(|| {
        Outcome::fail(format!(
            "The {} command requires the {} parameter",
            repr(command),
            repr(name)
        ))
    })
}

fn call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    let memory = &server.memory;
    let command = text(arguments, "command").unwrap_or_default();
    let path = text(arguments, "path");
    if command != "view" {
        // The mailbox and its links are a lifecycle's documents.
        for key in ["path", "old_path", "new_path"] {
            if let Some(value) = text(arguments, key) {
                match resolve(&memory.mounts, value) {
                    Ok((_, document)) if state_path(document) => return Outcome::fail(MAILBOX),
                    Ok(_) => {}
                    Err(error) => return corrective(&error).into(),
                }
            }
        }
    }
    let answered = match command {
        "view" => {
            let path = path.unwrap_or("/");
            let range = match arguments.get("view_range").and_then(Value::as_array) {
                None => None,
                Some(items) if items.len() == 2 => Some((
                    items[0].as_i64().unwrap_or_default(),
                    items[1].as_i64().unwrap_or_default(),
                )),
                Some(items) => match malformed_range(server, path, items) {
                    Some(refusal) => return refusal,
                    None => None,
                },
            };
            Call::View { path, range }
        }
        "create" => {
            let content = text(arguments, "content").or(text(arguments, "file_text"));
            match (
                required(path, command, "path"),
                required(content, command, "content"),
            ) {
                (Ok(path), Ok(content)) => Call::Create { path, content },
                (Err(refusal), _) | (_, Err(refusal)) => return refusal,
            }
        }
        "str_replace" => {
            let (old, new) = (text(arguments, "old_str"), text(arguments, "new_str"));
            match (
                required(path, command, "path"),
                required(old, command, "old_str"),
                required(new, command, "new_str"),
            ) {
                (Ok(path), Ok(old), Ok(new)) => Call::StrReplace { path, old, new },
                (Err(refusal), _, _) | (_, Err(refusal), _) | (_, _, Err(refusal)) => {
                    return refusal;
                }
            }
        }
        "insert" => {
            let line = integer(arguments, "insert_line");
            match (
                required(path, command, "path"),
                line.ok_or_else(|| required(None, command, "insert_line").unwrap_err()),
                required(text(arguments, "insert_text"), command, "insert_text"),
            ) {
                (Ok(path), Ok(line), Ok(text)) => Call::Insert { path, line, text },
                (Err(refusal), _, _) | (_, Err(refusal), _) | (_, _, Err(refusal)) => {
                    return refusal;
                }
            }
        }
        "delete" => match required(path, command, "path") {
            Ok(path) => Call::Delete { path },
            Err(refusal) => return refusal,
        },
        _ => match (
            required(text(arguments, "old_path"), command, "old_path"),
            required(text(arguments, "new_path"), command, "new_path"),
        ) {
            (Ok(old), Ok(new)) => Call::Rename { old, new },
            (Err(refusal), _) | (_, Err(refusal)) => return refusal,
        },
    };
    match dispatch(memory, &answered) {
        Ok(outcome) => {
            if let Some(viewed) = &outcome.viewed {
                server.observe(&viewed.scope, &viewed.path);
            }
            outcome.into()
        }
        Err(error) => Outcome::failed("memory", &error.to_string()),
    }
}

/// A `view_range` that is not two numbers: refused as `commands.view`
/// refuses it, which is only once a document is being viewed; the index
/// and a listing ignore it.
fn malformed_range(server: &Server, path: &str, items: &[Value]) -> Option<Outcome> {
    let memory = &server.memory;
    if path.trim_matches('/').is_empty() {
        return None;
    }
    let (mount, document) = resolve(&memory.mounts, path).ok()?;
    if document.is_empty() {
        return None;
    }
    let found = memory.store.read(&mount.scope, document).ok().flatten()?;
    if found.envelope.redacted {
        return None;
    }
    Some(Outcome::fail(format!(
        "view_range must be [start_line, end_line], got {}",
        py_str(&Value::Array(items.to_vec()))
    )))
}
