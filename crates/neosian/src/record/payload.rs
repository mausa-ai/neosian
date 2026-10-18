//! A foreign agent's span (`record/span.py`, DESIGN §20.9): hook payloads
//! in, one turn's messages out. Pure. What the spool keeps is the reduced
//! record of each hook call, never the payload; what the store gets is the
//! messages a Conversation would have written, in provider order, so the
//! audit view, the projector, recall and reflection work on a foreign
//! session unchanged. Transcript files are never read.

use serde_json::{Map, Value, json};

use crate::conversation::message::{Message, Role, ToolCall, falsy};
use crate::pyfmt::{dumps_spaced, py_str};
use crate::text::{head, len, tail};

pub const PROMPT_EVENT: &str = "UserPromptSubmit";
pub const TOOL_EVENT: &str = "PostToolUse";
pub const STOP_EVENT: &str = "Stop";
pub const SESSION_START_EVENT: &str = "SessionStart";
pub const RESPONSE_EVENT: &str = "AssistantResponse";
/// The record keeps a result whole below the ceiling and its head and
/// tail above it (N6): a span must stay under the wire's request ceiling,
/// and the read side pages (§9.6).
pub const TOOL_RESULT_CHARS: usize = 1 << 20;

pub type Payload = Map<String, Value>;
pub type Record = Map<String, Value>;

/// One hook payload; the failure names what was wrong with it.
pub fn parse_payload(text: &str, agent: &str) -> Result<Payload, String> {
    let raw: Value =
        serde_json::from_str(text).map_err(|error| format!("stdin is not JSON: {error}"))?;
    let Value::Object(mut raw) = raw else {
        return Err("stdin is not a JSON object".to_string());
    };
    if agent == "cursor" {
        raw = cursor_payload(raw);
    } else if agent == "claude-code" && raw.get("cursor_version").is_some_and(Value::is_string) {
        // Cursor imports Claude hooks too; only its native recorder owns a span.
        let session = raw.get("conversation_id").cloned().unwrap_or(Value::Null);
        raw.insert("session_id".to_string(), session);
        raw.insert("hook_event_name".to_string(), json!("CursorImported"));
    }
    match raw.get("session_id") {
        Some(Value::String(session)) if !session.is_empty() => Ok(raw),
        _ => Err("the payload carries no session_id".to_string()),
    }
}

/// Cursor's native hook vocabulary, reduced to the recorder's events.
fn cursor_payload(mut payload: Payload) -> Payload {
    let event = payload
        .get("hook_event_name")
        .cloned()
        .unwrap_or(Value::Null);
    let mapped = match event.as_str() {
        Some("beforeSubmitPrompt") => json!(PROMPT_EVENT),
        Some("postToolUse" | "afterMCPExecution") => json!(TOOL_EVENT),
        Some("afterAgentResponse") => json!(RESPONSE_EVENT),
        Some("stop") => json!(STOP_EVENT),
        Some("sessionStart") => json!(SESSION_START_EVENT),
        _ => event,
    };
    let response = payload
        .get("tool_output")
        .or(payload.get("result_json"))
        .cloned()
        .unwrap_or(Value::Null);
    let status = match payload.get("status") {
        Some(value) if !falsy(value) => value.clone(),
        _ => json!("completed"),
    };
    let session = payload
        .get("conversation_id")
        .cloned()
        .unwrap_or(Value::Null);
    payload.insert("session_id".to_string(), session);
    payload.insert("hook_event_name".to_string(), mapped);
    payload.insert("tool_response".to_string(), response);
    payload.insert("last_assistant_message".to_string(), json!(""));
    payload.insert("status".to_string(), status);
    payload
}

/// `str(payload.get(key) or "")`.
fn text_or_empty(payload: &Payload, key: &str) -> String {
    match payload.get(key) {
        Some(value) if !falsy(value) => py_str(value),
        _ => String::new(),
    }
}

fn record(pairs: Vec<(&str, Value)>) -> Record {
    pairs
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect()
}

/// The spool record a payload becomes, with its disposition: `spooled`
/// (a record), `skipped` (a subagent's round: its own session is not this
/// span), `ignored` (an event the record does not track).
pub fn reduce_payload(payload: &Payload) -> (&'static str, Option<Record>) {
    let spooled = |pairs| ("spooled", Some(record(pairs)));
    match payload.get("hook_event_name").and_then(Value::as_str) {
        Some("MailboxReceived") => spooled(vec![
            ("kind", json!("context")),
            ("text", json!(text_or_empty(payload, "text"))),
        ]),
        Some(PROMPT_EVENT) => {
            let text = match payload.get("prompt").filter(|value| !falsy(value)) {
                Some(value) => py_str(value),
                None => text_or_empty(payload, "user_prompt"),
            };
            spooled(vec![("kind", json!("prompt")), ("text", json!(text))])
        }
        Some(TOOL_EVENT) => {
            if payload.get("agent_id").is_some_and(|value| !falsy(value)) {
                return ("skipped", None);
            }
            let id = match payload.get("tool_use_id") {
                Some(Value::String(id)) if !id.is_empty() => json!(id),
                _ => Value::Null,
            };
            let name = match payload.get("tool_name").filter(|value| !falsy(value)) {
                Some(value) => py_str(value),
                None => "tool".to_string(),
            };
            let input = match payload.get("tool_input") {
                Some(object @ Value::Object(_)) => object.clone(),
                other => json!({"input": other.cloned().unwrap_or(Value::Null)}),
            };
            spooled(vec![
                ("kind", json!("tool")),
                ("id", id),
                ("name", json!(name)),
                ("input", input),
                (
                    "response",
                    json!(render_response(payload.get("tool_response"))),
                ),
            ])
        }
        Some(STOP_EVENT) => {
            let mut pairs = vec![
                ("kind", json!("stop")),
                (
                    "text",
                    json!(text_or_empty(payload, "last_assistant_message")),
                ),
            ];
            if payload.contains_key("status") {
                let status = match payload.get("status").filter(|value| !falsy(value)) {
                    Some(value) => py_str(value),
                    None => "completed".to_string(),
                };
                pairs.push(("status", json!(status)));
            }
            spooled(pairs)
        }
        Some(RESPONSE_EVENT) => spooled(vec![
            ("kind", json!("assistant")),
            ("text", json!(text_or_empty(payload, "text"))),
        ]),
        _ => ("ignored", None),
    }
}

/// A tool's response as the record keeps it: the `text` of a text
/// object, a string as is, nothing for none, any other value as sorted
/// JSON; then the ceiling.
fn render_response(value: Option<&Value>) -> String {
    let text = match value {
        Some(Value::Object(map)) if map.get("text").is_some_and(Value::is_string) => {
            map["text"].as_str().unwrap_or_default().to_string()
        }
        Some(Value::String(text)) => text.clone(),
        None | Some(Value::Null) => String::new(),
        Some(other) => dumps_spaced(other, false, true),
    };
    head_and_tail(&text, TOOL_RESULT_CHARS)
}

/// `text` whole when it fits `limit`, else half of `limit` from each end
/// around a marker naming what the record does not hold.
pub fn head_and_tail(text: &str, limit: usize) -> String {
    let total = len(text);
    if total <= limit {
        return text.to_string();
    }
    let half = limit / 2;
    let marker = format!("\n… [{} chars omitted of {total}] …\n", total - 2 * half);
    format!("{}{marker}{}", head(text, half), tail(text, total - half))
}

/// The span as the messages a run would have produced, in order.
pub fn messages_of(records: &[Record]) -> Vec<Message> {
    let mut messages = Vec::new();
    let mut rounds = 0;
    let text = |record: &Record, key: &str| record.get(key).map_or_else(String::new, py_str);
    let prose = Message::text;
    for record in records {
        match record.get("kind").and_then(Value::as_str) {
            Some("prompt" | "context") => messages.push(prose(Role::User, text(record, "text"))),
            Some("tool") => {
                rounds += 1;
                let call_id = match record.get("id") {
                    Some(id) if !falsy(id) => py_str(id),
                    _ => format!("round-{rounds}"),
                };
                let call = ToolCall {
                    id: call_id.clone(),
                    name: text(record, "name"),
                    arguments: record.get("input").cloned().unwrap_or(Value::Null),
                    extra: Value::Null,
                };
                messages.push(Message {
                    role: Role::Assistant,
                    content: None,
                    reasoning: Value::Null,
                    tool_calls: vec![call],
                    tool_call_id: None,
                    extra: Value::Null,
                });
                messages.push(Message {
                    tool_call_id: Some(call_id),
                    ..Message::text(Role::Tool, text(record, "response"))
                });
            }
            Some("stop" | "assistant") if record.get("text").is_some_and(|t| !falsy(t)) => {
                messages.push(prose(Role::Assistant, text(record, "text")));
            }
            _ => {}
        }
    }
    messages
}

/// The last prompt of the span, the sessions document's line.
pub fn last_prompt(records: &[Record]) -> Option<String> {
    records
        .iter()
        .rfind(|record| record.get("kind").and_then(Value::as_str) == Some("prompt"))
        .map(|record| record.get("text").map_or_else(String::new, py_str))
}

#[cfg(test)]
mod tests;
