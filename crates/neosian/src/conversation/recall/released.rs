//! The released whole-turn rendering (`conversation/projection.py`'s
//! `render_turn`, `render_call`, `call_ids`, DESIGN §9.6, §33), kept for a
//! stored turn records cannot carry (a call id, tool name or
//! `tool_call_id` over 1,024 bytes, §40.54 item 10): such a turn is read
//! whole and shown as recall showed it before views, after one fixed line
//! saying so, and `call` answers as it did. Its pieces have no record
//! numbers, so `seq` and later parts are refused.

use std::collections::HashMap;

use super::{Ask, Corrective, Recalled, Selector, hint, marker};
use crate::conversation::Turn;
use crate::conversation::message::{Role, text_of};
use crate::conversation::projection::REDACTED;
use crate::pyfmt::dumps;
use crate::repr::repr;
use crate::text::{head, len, strip, tail};

pub const RESULT_CHARS: usize = 4096;

fn omitted(count: usize, call: &str) -> String {
    format!(
        " … [{count} chars omitted; pass call=\"{call}\" to recall_turn to read this result whole] … "
    )
}

/// Head and tail (half of `limit` each) around the marker; a result with
/// no call id cannot be opened whole, so it stays whole here.
fn paged(text: &str, call: Option<&str>, limit: usize) -> String {
    let total = len(text);
    let Some(call) = call.filter(|_| total > limit) else {
        return text.to_string();
    };
    let half = limit / 2;
    format!(
        "{}{}{}",
        head(text, half),
        omitted(total - 2 * half, call),
        tail(text, total - half)
    )
}

fn args(arguments: &serde_json::Value) -> String {
    dumps(arguments, true)
}

/// The verbatim role-labeled turn: prose unclipped, each call as it was
/// made, a tool result over `result_chars` shown head and tail; a
/// redacted turn says so and nothing else.
pub fn render_turn(turn: &Turn, result_chars: usize) -> String {
    if turn.redacted {
        return format!("Turn {}: {REDACTED}", turn.turn);
    }
    let names: HashMap<&str, &str> = turn
        .messages
        .iter()
        .flat_map(|message| &message.tool_calls)
        .map(|call| (call.id.as_str(), call.name.as_str()))
        .collect();
    let mut lines = vec![format!("Turn {} (verbatim):", turn.turn)];
    for message in &turn.messages {
        match message.role {
            Role::User => lines.push(format!("USER: {}", text_of(message))),
            Role::Assistant => {
                let prose = text_of(message);
                let prose = strip(&prose);
                if !prose.is_empty() {
                    lines.push(format!("AGENT: {prose}"));
                }
                for call in &message.tool_calls {
                    lines.push(format!(
                        "AGENT calls {}({})",
                        call.name,
                        args(&call.arguments)
                    ));
                }
            }
            Role::Tool => {
                let call_id = message.tool_call_id.as_deref();
                let name = call_id.map_or("tool", |id| names.get(id).copied().unwrap_or("tool"));
                let result = paged(&text_of(message), call_id, result_chars);
                lines.push(format!("TOOL {name} → {result}"));
            }
            Role::System => {}
        }
    }
    lines.join("\n")
}

/// The ids of the turn's tool calls, in order.
pub fn call_ids(turn: &Turn) -> Vec<&str> {
    turn.messages
        .iter()
        .flat_map(|message| &message.tool_calls)
        .map(|call| call.id.as_str())
        .collect()
}

/// One call of the turn and its result, whole; None when the turn made
/// no such call.
pub fn render_call(turn: &Turn, call: &str) -> Option<String> {
    let results: HashMap<&str, String> = turn
        .messages
        .iter()
        .filter(|message| message.role == Role::Tool)
        .map(|message| {
            (
                message.tool_call_id.as_deref().unwrap_or(""),
                text_of(message).into_owned(),
            )
        })
        .collect();
    turn.messages
        .iter()
        .flat_map(|message| &message.tool_calls)
        .find(|made| made.id == call)
        .map(|made| {
            let result = results.get(made.id.as_str()).map_or("?", String::as_str);
            format!(
                "Turn {}, call {call} (verbatim):\nAGENT calls {}({})\nTOOL {} → {result}",
                turn.turn,
                made.name,
                args(&made.arguments),
                made.name
            )
        })
}

/// `ask` answered from the whole turn: any view as the fixed line and the
/// released rendering; `call` (part 0) as released; nothing else.
pub fn answer(turn: &Turn, ask: &Ask) -> Result<Recalled, Corrective> {
    let number = turn.turn;
    let call = match &ask.part {
        None => {
            let text = format!(
                "{}\n{}",
                marker("recall_turn_whole", &[]),
                render_turn(turn, RESULT_CHARS)
            );
            return Ok(Recalled { text, view: true });
        }
        Some((Selector::Call(call), 0)) => call,
        Some(_) => {
            let error = format!("Turn {number} has no parts to open");
            return Err(Corrective::new(error, Some(hint("recall_turn_whole_hint"))));
        }
    };
    match render_call(turn, call) {
        Some(text) => Ok(Recalled { text, view: true }),
        None => {
            let ids = call_ids(turn).join(", ");
            let listed = if ids.is_empty() {
                "none".to_string()
            } else {
                ids
            };
            let error = format!("Turn {number} made no call {}", repr(call));
            Err(Corrective::new(
                error,
                Some(format!("Its calls: {listed}.")),
            ))
        }
    }
}
