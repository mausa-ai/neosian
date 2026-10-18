//! `recall_turn`'s renderings (`conversation/projection.py`'s
//! `render_turn`, `render_call`, `call_ids`, and `recall.py`'s `recall`,
//! DESIGN §9.6, §33): the verbatim role-labeled turn with a long tool
//! result paged, the marker naming the call that opens it whole.

use std::collections::HashMap;

use super::message::{Role, text_of};
use super::projection::REDACTED;
use super::{Error, Turn};
use crate::pyfmt::dumps;
use crate::store::FileStore;
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

/// A corrective failure as the tool answers it.
pub struct Corrective {
    pub error: String,
    pub hint: Option<String>,
}

/// Turn `turn` of `target`, verbatim (one call of it whole when `call`
/// names it), or the corrective failure with `label` naming the
/// conversation; a refusal of the seam is `[code] message`.
pub fn recall(
    store: &FileStore,
    target: &str,
    turn: i64,
    label: &str,
    call: Option<&str>,
) -> Result<Result<String, Corrective>, Error> {
    if turn < 1 {
        return Ok(Err(Corrective {
            error: format!("Turn numbers start at 1; got {turn}"),
            hint: Some(
                "Turn numbers appear in square brackets in the conversation log, e.g. [7] or [3-6]."
                    .to_string(),
            ),
        }));
    }
    let turns = match store.read_turns(target, 0, None) {
        Ok(turns) => turns,
        Err(Error::Conversation(refused)) => {
            return Ok(Err(Corrective {
                error: format!("[{}] {}", refused.code, refused.message),
                hint: None,
            }));
        }
        Err(error) => return Err(error),
    };
    let Some(found) = turns.iter().find(|found| found.turn as i64 == turn) else {
        let last = turns.last().map_or(0, |found| found.turn);
        return Ok(Err(Corrective {
            error: format!("Turn {turn} does not exist"),
            hint: Some(format!("{label} has turns 1-{last}.")),
        }));
    };
    let Some(call) = call else {
        return Ok(Ok(render_turn(found, RESULT_CHARS)));
    };
    Ok(match render_call(found, call) {
        Some(text) => Ok(text),
        None => Err(Corrective {
            error: format!("Turn {turn} made no call {}", crate::repr::repr(call)),
            hint: Some(format!(
                "Its calls: {}.",
                match call_ids(found).join(", ") {
                    ids if ids.is_empty() => "none".to_string(),
                    ids => ids,
                }
            )),
        }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::conversation::message::from_json;
    use crate::store::time::Stamp;
    use serde_json::json;

    fn turn(messages: Vec<serde_json::Value>) -> Turn {
        Turn {
            conversation_id: "s1".to_string(),
            turn: 2,
            messages: messages.iter().map(|m| from_json(m).unwrap()).collect(),
            created_at: Stamp::parse("2026-10-12T22:27:56Z").unwrap(),
            actor: None,
            redacted: false,
        }
    }

    #[test]
    fn a_turn_renders_verbatim_with_a_long_result_paged() {
        let long = "x".repeat(5000);
        let made = turn(vec![
            json!({"role": "user", "content": "do it é", "reasoning": null, "tool_calls": [], "tool_call_id": null}),
            json!({"role": "assistant", "content": "  thinking  ", "reasoning": null, "tool_calls": [{"id": "t1", "name": "Read", "arguments": {"path": "/a b", "n": 1}}], "tool_call_id": null}),
            json!({"role": "tool", "content": long, "reasoning": null, "tool_calls": [], "tool_call_id": "t1"}),
            json!({"role": "tool", "content": "orphan", "reasoning": null, "tool_calls": [], "tool_call_id": null}),
            json!({"role": "assistant", "content": "done", "reasoning": null, "tool_calls": [], "tool_call_id": null}),
        ]);
        let text = render_turn(&made, RESULT_CHARS);
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines[0], "Turn 2 (verbatim):");
        assert_eq!(lines[1], "USER: do it é");
        assert_eq!(lines[2], "AGENT: thinking");
        assert_eq!(lines[3], "AGENT calls Read({\"path\":\"/a b\",\"n\":1})");
        assert!(lines[4].starts_with(&format!("TOOL Read → {}", "x".repeat(2048))));
        assert!(lines[4].contains(
            " … [904 chars omitted; pass call=\"t1\" to recall_turn to read this result whole] … "
        ));
        assert!(lines[4].ends_with(&"x".repeat(2048)));
        assert_eq!(lines[5], "TOOL tool → orphan");
        assert_eq!(lines[6], "AGENT: done");
        assert_eq!(call_ids(&made), ["t1"]);
        let whole = render_call(&made, "t1").unwrap();
        assert!(whole.starts_with("Turn 2, call t1 (verbatim):\nAGENT calls Read({\"path\":\"/a b\",\"n\":1})\nTOOL Read → xxxx"));
        assert!(whole.ends_with(&"x".repeat(5000)));
        assert!(render_call(&made, "t9").is_none());
        let mut blanked = turn(vec![]);
        blanked.redacted = true;
        assert_eq!(render_turn(&blanked, RESULT_CHARS), "Turn 2: [redacted]");
    }
}
