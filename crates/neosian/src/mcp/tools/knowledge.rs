//! Governed retrieval and proposal preparation, using ordinary mounted authority.

use serde_json::{Map, Value};

use super::{Outcome, Tool, integer, text};
use crate::assets::{mapping, quoted};
use crate::knowledge::{govern, governance::Policy, model::invalid, read};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;

const ASSET: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/core-tools.yaml"));
const FIELDS: [Field; 6] = [
    Field {
        name: "command",
        kind: Kind::Literal(&["read", "references", "prepare"]),
        presence: Presence::Required,
    },
    Field {
        name: "path",
        kind: Kind::Str,
        presence: Presence::Required,
    },
    Field {
        name: "policy",
        kind: Kind::Literal(&["all", "effective", "approved"]),
        presence: Presence::Default("effective"),
    },
    Field {
        name: "data",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "expected_version",
        kind: Kind::StrictInt,
        presence: Presence::Optional,
    },
    Field {
        name: "reason",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
];

pub fn tool() -> Tool {
    Tool {
        name: "knowledge",
        description: quoted(ASSET, "knowledge").expect("knowledge description"),
        params: mapping(ASSET, "knowledge_params").expect("knowledge parameters"),
        fields: &FIELDS,
        read_only: true,
        destructive: false,
        call,
    }
}

fn call(server: &Server, args: &Map<String, Value>) -> Outcome {
    let result = (|| {
        let path = text(args, "path").expect("validated path");
        let policy: Policy = serde_json::from_value(args["policy"].clone())
            .map_err(|_| invalid("invalid knowledge policy"))?;
        let command = text(args, "command").expect("command");
        if command != "prepare"
            && ["data", "expected_version", "reason"]
                .iter()
                .any(|k| args.get(*k).is_some_and(|v| !v.is_null()))
        {
            return Err(invalid("read and references take no review change fields"));
        }
        match command {
            "read" => govern::read(&server.memory, path, policy),
            "references" => read::references(&server.memory, path, policy).map(|(value, _)| value),
            _ => {
                let change = govern::Change::parse(
                    text(args, "data")
                        .ok_or_else(|| invalid("prepare requires governance JSON in data"))?,
                )?;
                let expected = integer(args, "expected_version")
                    .and_then(|n| u64::try_from(n).ok())
                    .filter(|v| *v > 0)
                    .ok_or_else(|| invalid("prepare requires a positive expected_version"))?;
                let reason =
                    text(args, "reason").ok_or_else(|| invalid("prepare requires a reason"))?;
                Ok(serde_json::to_value(govern::prepare(
                    &server.memory,
                    path,
                    &change,
                    expected,
                    reason,
                )?)
                .expect("proposal serializes"))
            }
        }
    })();
    match result {
        Ok(value) => {
            // A delivered body is a read (§40.33); a policy-withheld one is not.
            if text(args, "command") == Some("read")
                && value["included"] == true
                && let (Some(scope), Some(path)) = (value["scope"].as_str(), value["path"].as_str())
            {
                server.observe(scope, path);
            }
            Outcome::ok(value.to_string())
        }
        Err(error) => Outcome::failed("knowledge", &crate::conversation::Error::from(error).text()),
    }
}
