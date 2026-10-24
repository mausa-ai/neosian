//! MCP schema for the shared mailbox command service.

use super::{Outcome, Tool};
use crate::assets::{MESSAGES, block, mapping};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::messaging::dispatch::{self, COMMANDS, DELIVERIES, STATUSES};
use serde_json::{Map, Value};

const FIELDS: [Field; 21] = [
    Field {
        name: "command",
        kind: Kind::Literal(&COMMANDS),
        presence: Presence::Required,
    },
    Field {
        name: "scope",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "session",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "message_id",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "body",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "conversation",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "turn",
        kind: Kind::Int,
        presence: Presence::Optional,
    },
    Field {
        name: "delivery",
        kind: Kind::Literal(&DELIVERIES),
        presence: Presence::Default("next_activity"),
    },
    Field {
        name: "actionable",
        kind: Kind::Bool,
        presence: Presence::Default("false"),
    },
    Field {
        name: "due_at",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "delay_seconds",
        kind: Kind::Int,
        presence: Presence::Optional,
    },
    Field {
        name: "about",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "about_turn",
        kind: Kind::Int,
        presence: Presence::Optional,
    },
    Field {
        name: "context",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "context_version",
        kind: Kind::StrictInt,
        presence: Presence::Optional,
    },
    Field {
        name: "change_id",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "occurrence",
        kind: Kind::Int,
        presence: Presence::Optional,
    },
    Field {
        name: "token",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "outcome",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "status",
        kind: Kind::Literal(&STATUSES),
        presence: Presence::Default("unread"),
    },
    Field {
        name: "limit",
        kind: Kind::Int,
        presence: Presence::Default("50"),
    },
];

pub fn tool() -> Tool {
    Tool {
        name: "messages",
        description: block(MESSAGES, "tool").expect("messages.yaml carries the tool"),
        params: mapping(MESSAGES, "params").expect("messages.yaml carries the params"),
        fields: &FIELDS,
        read_only: false,
        destructive: false,
        call,
    }
}

fn call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    dispatch::dispatch(&server.memory, arguments).into()
}
