//! A bounded current-memory search with mounted authorization and references.

use serde_json::{Map, Value};

use super::{Outcome, Tool};
use crate::assets::{mapping, quoted};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::memory::search::{Request, mounted};

const ASSET: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/core-tools.yaml"));
const FIELDS: [Field; 7] = [
    Field {
        name: "knowledge_policy",
        kind: Kind::Literal(&["all", "effective", "approved"]),
        presence: Presence::Default("all"),
    },
    Field {
        name: "query",
        kind: Kind::Str,
        presence: Presence::Required,
    },
    Field {
        name: "path",
        kind: Kind::Str,
        presence: Presence::Default("/"),
    },
    Field {
        name: "fields",
        kind: Kind::StrList,
        presence: Presence::Default("[\"path\",\"content\"]"),
    },
    Field {
        name: "metadata_keys",
        kind: Kind::StrList,
        presence: Presence::Default("[]"),
    },
    Field {
        name: "limit",
        kind: Kind::StrictInt,
        presence: Presence::Default("20"),
    },
    Field {
        name: "cursor",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
];

pub fn tool() -> Tool {
    Tool {
        name: "search_memory",
        description: quoted(ASSET, "search_memory").expect("search description"),
        params: mapping(ASSET, "search_memory_params").expect("search parameters"),
        fields: &FIELDS,
        read_only: true,
        destructive: false,
        call,
    }
}

fn call(server: &Server, args: &Map<String, Value>) -> Outcome {
    let mut args = args.clone();
    let path = args.remove("path").expect("validated path");
    let request: Request = match serde_json::from_value(Value::Object(args)) {
        Ok(request) => request,
        Err(error) => return Outcome::fail(error.to_string()),
    };
    match mounted::run(
        &server.memory,
        path.as_str().expect("path string"),
        &request,
    ) {
        Ok(page) => Outcome::ok(serde_json::to_string(&page).expect("search page serializes")),
        Err(error) => Outcome::failed(
            "search_memory",
            &crate::conversation::Error::from(error).text(),
        ),
    }
}
