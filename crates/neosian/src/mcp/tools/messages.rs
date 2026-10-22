//! The `messages` tool (`messaging/tools.py`, DESIGN §34): the same
//! mailbox operations the Python tool serves, a receipt as
//! `json.dumps` of the message with its version, every refusal the
//! sentence the Python side raises.

use serde_json::{Map, Value, json};

use super::{Outcome, Tool, integer, text};
use crate::assets::{MESSAGES, block, mapping};
use crate::conversation::Error;
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::messaging::mailbox::{ListStatus, Mailbox, Receipt};
use crate::messaging::types::{Action, Delivery, check_target, to_value};
use crate::messaging::writes::{Send, Update};
use crate::pyfmt::dumps_spaced;
use crate::repr::repr;
use crate::store::time::{Stamp, Unreadable};

const COMMANDS: [&str; 10] = [
    "send", "list", "view", "ack", "claim", "renew", "release", "complete", "snooze", "cancel",
];
const DELIVERIES: [&str; 2] = ["next_activity", "wake"];
const STATUSES: [&str; 5] = ["unread", "open", "scheduled", "closed", "all"];

const FIELDS: [Field; 18] = [
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

fn receipt_json(receipt: &Receipt) -> Map<String, Value> {
    let mut value = Map::new();
    value.insert("version".to_string(), json!(receipt.version));
    value.extend(to_value(&receipt.message));
    value
}

fn dumps(value: &Map<String, Value>) -> String {
    dumps_spaced(&Value::Object(value.clone()), true, false)
}

/// The Python tool's `except (NeosianError, ValueError)`: the sentence.
fn refused(error: Error) -> Outcome {
    match error {
        Error::Value(sentence) => Outcome::fail(sentence),
        Error::Memory(error) => Outcome::fail(error.message),
        Error::Conversation(refused) => Outcome::fail(refused.message),
        Error::Io(error) => Outcome::failed("messages", &error.to_string()),
    }
}

/// `datetime.fromisoformat(due_at)`, then `utc()`'s rule.
fn moment(text: Option<&str>) -> Result<Option<Stamp>, Outcome> {
    match text {
        None => Ok(None),
        Some(text) => match Stamp::parse(text) {
            Ok(stamp) => Ok(Some(stamp)),
            Err(Unreadable::Naive) => {
                Err(Outcome::fail("mailbox timestamps must be timezone-aware"))
            }
            Err(Unreadable::Malformed) => Err(Outcome::fail(format!(
                "Invalid isoformat string: {}",
                repr(text)
            ))),
        },
    }
}

fn call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    let memory = &server.memory;
    let command = text(arguments, "command").unwrap_or_default();
    let mailbox = Mailbox::of_mounts(
        memory.store.as_ref(),
        &memory.mounts,
        text(arguments, "session"),
    )
    .with_actor(memory.actor.clone());
    let scope = text(arguments, "scope");
    let selected = scope.map(|scope| {
        memory
            .mounts
            .iter()
            .find(|mount| scope == mount.path || scope == format!("/{}", mount.path))
            .map_or(scope, |mount| mount.scope.as_str())
    });
    let status = ListStatus::parse(text(arguments, "status").unwrap_or("unread"))
        .unwrap_or(ListStatus::Unread);
    let limit = integer(arguments, "limit").unwrap_or(50);
    if command == "list" {
        let limit = usize::try_from(limit).unwrap_or(0);
        return match mailbox.list(selected, status, limit, true, false) {
            Ok(receipts) => {
                let items: Vec<Value> = receipts
                    .iter()
                    .map(|receipt| Value::Object(receipt_json(receipt)))
                    .collect();
                let mut answer = Map::new();
                answer.insert("items".to_string(), Value::Array(items));
                Outcome::ok(dumps(&answer))
            }
            Err(error) => refused(error),
        };
    }
    let Some(selected) = selected else {
        return Outcome::fail("scope is required");
    };
    let due_at = match moment(text(arguments, "due_at")) {
        Ok(due_at) => due_at,
        Err(refusal) => return refusal,
    };
    let delay_seconds = integer(arguments, "delay_seconds");
    if command == "send" {
        let (conversation, turn) = (text(arguments, "conversation"), integer(arguments, "turn"));
        if let Err(sentence) = check_target(selected, conversation, turn) {
            return Outcome::fail(sentence);
        }
        let send = Send {
            scope: selected,
            conversation,
            turn,
            delivery: Delivery::parse(text(arguments, "delivery").unwrap_or("next_activity"))
                .unwrap_or(Delivery::NextActivity),
            actionable: arguments
                .get("actionable")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            due_at,
            delay_seconds,
            about: text(arguments, "about"),
            about_turn: integer(arguments, "about_turn"),
        };
        return match mailbox.send(text(arguments, "body").unwrap_or(""), &send) {
            Ok(receipt) => Outcome::ok(dumps(&receipt_json(&receipt))),
            Err(error) => refused(error),
        };
    }
    let Some(message_id) = text(arguments, "message_id") else {
        return Outcome::fail("message_id is required");
    };
    if command == "view" {
        return match mailbox.view(selected, message_id).and_then(|receipt| {
            let history = mailbox.history(selected, message_id)?;
            Ok((receipt, history))
        }) {
            Ok((receipt, history)) => {
                let mut answer = receipt_json(&receipt);
                let versions: Vec<Value> = history
                    .iter()
                    .map(|item| Value::Object(to_value(item)))
                    .collect();
                answer.insert("history".to_string(), Value::Array(versions));
                Outcome::ok(dumps(&answer))
            }
            Err(error) => refused(error),
        };
    }
    let Some(occurrence) = integer(arguments, "occurrence") else {
        return Outcome::fail("occurrence is required; view the message first");
    };
    let action = Action::parse(command).expect("the literal admits an action");
    let update = Update {
        occurrence,
        token: text(arguments, "token"),
        outcome: text(arguments, "outcome"),
        due_at,
        delay_seconds,
    };
    match mailbox.update(selected, message_id, action, &update) {
        Ok(receipt) => Outcome::ok(dumps(&receipt_json(&receipt))),
        Err(error) => refused(error),
    }
}
