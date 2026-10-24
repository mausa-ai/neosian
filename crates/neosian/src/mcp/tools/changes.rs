use super::{Outcome, Tool};
use crate::assets::{mapping, quoted};
use crate::changes::{model::Request, mounted};
use crate::mcp::{
    schema::{Field, Kind, Presence},
    server::Server,
};
use serde_json::{Map, Value};

const ASSET: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/core-tools.yaml"));
const FIELDS: [Field; 3] = [
    Field {
        name: "path",
        kind: Kind::Str,
        presence: Presence::Required,
    },
    Field {
        name: "limit",
        kind: Kind::StrictInt,
        presence: Presence::Default("50"),
    },
    Field {
        name: "cursor",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
];

pub fn tool() -> Tool {
    Tool {
        name: "changes",
        description: quoted(ASSET, "changes").expect("changes description"),
        params: mapping(ASSET, "changes_params").expect("changes parameters"),
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
    match mounted::read(
        &server.memory,
        path.as_str().expect("path string"),
        &request,
    ) {
        Ok(page) => Outcome::ok(page.to_string()),
        Err(error) => Outcome::failed("changes", &crate::conversation::Error::from(error).text()),
    }
}
