//! The `harness` tool's declaration (§40.50): its prose from
//! `assets/prompts/harness.yaml`, its schema, and the call handed to the
//! server's registry. Declared only on the stdio server that enabled it.

use serde_json::{Map, Value};

use super::{Outcome, Tool};
use crate::harness::adapter::{Agent, Mode};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;

pub const NAME: &str = "harness";
const COMMANDS: [&str; 5] = ["start", "wait", "send", "stop", "list"];

const FIELDS: [Field; 9] = [
    Field {
        name: "command",
        kind: Kind::Literal(&COMMANDS),
        presence: Presence::Required,
    },
    Field {
        name: "agent",
        kind: Kind::Literal(&Agent::NAMES),
        presence: Presence::Optional,
    },
    Field {
        name: "prompt",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "mode",
        kind: Kind::Literal(&Mode::NAMES),
        presence: Presence::Default("read-only"),
    },
    Field {
        name: "model",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "directory",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "child",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "session",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "seconds",
        kind: Kind::Int,
        presence: Presence::Default("10"),
    },
];

pub fn tool() -> Tool {
    Tool {
        name: NAME,
        description: crate::harness::text::description(),
        params: crate::harness::text::params(),
        fields: &FIELDS,
        read_only: false,
        destructive: true,
        call,
    }
}

fn call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    match server.harness() {
        Some(registry) => registry.call(&server.memory, arguments).into(),
        None => Outcome::fail(format!("Tool '{NAME}' not found")),
    }
}
