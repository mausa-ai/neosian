//! Agent review and explicit apply use the same guards as the operator CLI.

use serde_json::{Map, Value};

use super::{Outcome, Tool, integer, text};
use crate::assets::{mapping, quoted};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::store::error::Error;
use crate::upkeep::model::{Proposal, Request, parse};

const ASSET: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/core-tools.yaml"));
const REVIEW: [Field; 5] = [
    Field {
        name: "command",
        kind: Kind::Literal(&["scan", "prepare", "inspect"]),
        presence: Presence::Required,
    },
    Field {
        name: "data",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "path",
        kind: Kind::Str,
        presence: Presence::Default("/"),
    },
    Field {
        name: "limit",
        kind: Kind::StrictInt,
        presence: Presence::Default("100"),
    },
    Field {
        name: "stale_days",
        kind: Kind::StrictInt,
        presence: Presence::Default("90"),
    },
];
const APPLY: [Field; 3] = [
    Field {
        name: "proposal",
        kind: Kind::Str,
        presence: Presence::Required,
    },
    Field {
        name: "write",
        kind: Kind::Bool,
        presence: Presence::Default("false"),
    },
    Field {
        name: "approve",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
];

pub fn review() -> Tool {
    Tool {
        name: "upkeep_review",
        description: quoted(ASSET, "upkeep_review").expect("review description"),
        params: mapping(ASSET, "upkeep_review_params").expect("review parameters"),
        fields: &REVIEW,
        read_only: true,
        destructive: false,
        call: review_call,
    }
}

pub fn apply() -> Tool {
    Tool {
        name: "upkeep_apply",
        description: quoted(ASSET, "upkeep_apply").expect("apply description"),
        params: mapping(ASSET, "upkeep_apply_params").expect("apply parameters"),
        fields: &APPLY,
        read_only: false,
        destructive: true,
        call: apply_call,
    }
}

fn answer(name: &str, result: Result<(Value, u8), Error>) -> Outcome {
    match result {
        Ok((value, 0)) => Outcome::ok(value.to_string()),
        Ok((value, _)) => Outcome::fail(value.to_string()),
        Err(error) => Outcome::failed(name, &crate::conversation::Error::from(error).text()),
    }
}

fn review_call(server: &Server, args: &Map<String, Value>) -> Outcome {
    let result = (|| {
        let invalid = crate::knowledge::model::invalid;
        let command = text(args, "command").expect("validated command");
        let data = text(args, "data");
        let value = match command {
            "scan" => {
                if data.is_some() {
                    return Err(invalid("scan takes no request data"));
                }
                let limit = integer(args, "limit")
                    .and_then(|n| usize::try_from(n).ok())
                    .ok_or_else(|| invalid("invalid limit"))?;
                let days = integer(args, "stale_days")
                    .and_then(|n| u32::try_from(n).ok())
                    .ok_or_else(|| invalid("invalid stale_days"))?;
                crate::upkeep::scan(
                    &server.memory,
                    text(args, "path").expect("path"),
                    limit,
                    days,
                )?
            }
            "prepare" => {
                let request: Request =
                    parse(data.ok_or_else(|| invalid("prepare requires request JSON in data"))?)?;
                serde_json::to_value(crate::upkeep::prepare(&server.memory, &request)?)
                    .expect("proposal serializes")
            }
            _ => {
                let proposal: Proposal =
                    parse(data.ok_or_else(|| invalid("inspect requires proposal JSON in data"))?)?;
                crate::upkeep::inspect(&server.memory, &proposal)?
            }
        };
        Ok((value, 0))
    })();
    answer("upkeep_review", result)
}

fn apply_call(server: &Server, args: &Map<String, Value>) -> Outcome {
    let result = (|| {
        let proposal: Proposal = parse(text(args, "proposal").expect("proposal string"))?;
        crate::upkeep::apply(
            &server.memory,
            &proposal,
            args["write"].as_bool().expect("boolean"),
            text(args, "approve"),
        )
    })();
    answer("upkeep_apply", result)
}
