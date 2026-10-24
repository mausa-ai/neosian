//! One mailbox command service shared by the CLI and MCP.

use super::mailbox::{ListStatus, Mailbox, Receipt};
use super::types::{Action, Delivery, check_target, to_value};
use super::writes::{Send, Update};
use crate::conversation::Error;
use crate::memory::dispatch::{Memory, Outcome};
use crate::pyfmt::dumps_spaced;
use crate::repr::repr;
use crate::store::time::{Stamp, Unreadable};
use serde_json::{Map, Value, json};

pub const COMMANDS: [&str; 10] = [
    "send", "list", "view", "ack", "claim", "renew", "release", "complete", "snooze", "cancel",
];
pub const DELIVERIES: [&str; 2] = ["next_activity", "wake"];
pub const STATUSES: [&str; 5] = ["unread", "open", "scheduled", "closed", "all"];

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
        Error::Io(error) => Outcome::fail(format!("Tool 'messages' failed: {error}")),
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

pub fn dispatch(memory: &Memory, arguments: &Map<String, Value>) -> Outcome {
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
            context: match super::context::select(memory, arguments) {
                Ok(reference) => reference,
                Err(message) => return Outcome::fail(message),
            },
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

fn text<'a>(arguments: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    arguments.get(key).and_then(Value::as_str)
}
fn integer(arguments: &Map<String, Value>, key: &str) -> Option<i64> {
    arguments.get(key).and_then(Value::as_i64)
}
