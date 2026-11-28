//! What a harness's headless output says about the conversation itself,
//! harness by harness, for the watcher's record (X4a): the session, each
//! completed tool call with its input and result, the child's words, and
//! whether the provider completed the turn. This reads the native stream as
//! the harness printed it, never the bounded display `events.rs` keeps, and
//! it keeps what the child's own neosian hooks would have handed the record
//! verb: Claude Code's PostToolUse fires only for a tool that succeeded, its
//! structured result is the hook's `tool_response`, and a subagent's rounds
//! are not this session's.

use serde_json::{Map, Value, json};

use super::adapter::Agent;

/// One thing a line of native output said about the conversation.
#[derive(Clone, Debug, PartialEq)]
pub enum Said {
    /// The harness's own session ID.
    Session(String),
    /// Claude Code: a tool call whose result arrives on a later line.
    Call {
        id: String,
        name: String,
        input: Value,
    },
    /// Claude Code: the result of call `id`; `ok` is false for an error.
    Result {
        id: String,
        response: Value,
        ok: bool,
    },
    /// A completed tool call with its result, on one line.
    Tool {
        id: Option<String>,
        name: String,
        input: Value,
        response: Value,
    },
    /// Text the child wrote.
    Text(String),
    /// OpenCode: a new step begins; the answer is the newest step's text.
    Step,
    /// Claude Code: the turn's own final text.
    Answer(String),
    /// The provider completed the turn.
    Completed,
    /// The provider reported that the turn failed.
    Failed,
}

fn text(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str)
}

/// One line of native output, read for the record.
pub fn read(agent: Agent, line: &str) -> Vec<Said> {
    let Ok(Value::Object(event)) = serde_json::from_str(line) else {
        return Vec::new();
    };
    match agent {
        Agent::ClaudeCode => claude(&event),
        Agent::Codex => codex(&event),
        Agent::OpenCode => opencode(&event),
    }
}

fn blocks(event: &Map<String, Value>) -> &[Value] {
    event
        .get("message")
        .and_then(|message| message.get("content"))
        .and_then(Value::as_array)
        .map_or(&[], Vec::as_slice)
}

/// Claude Code 2.1.330's stream-json (record/x2b-captures-2026-11-21).
fn claude(event: &Map<String, Value>) -> Vec<Said> {
    let subagent = event
        .get("parent_tool_use_id")
        .is_some_and(|id| !id.is_null());
    let mut said = Vec::new();
    match text(event.get("type")).unwrap_or_default() {
        "system" if text(event.get("subtype")) == Some("init") => {
            if let Some(session) = text(event.get("session_id")) {
                said.push(Said::Session(session.to_string()));
            }
        }
        "assistant" if !subagent => {
            for block in blocks(event) {
                match text(block.get("type")) {
                    Some("tool_use") => {
                        if let Some(id) = text(block.get("id")) {
                            said.push(Said::Call {
                                id: id.to_string(),
                                name: text(block.get("name")).unwrap_or("tool").to_string(),
                                input: block.get("input").cloned().unwrap_or(Value::Null),
                            });
                        }
                    }
                    Some("text") => {
                        if let Some(words) = text(block.get("text")).filter(|t| !t.is_empty()) {
                            said.push(Said::Text(words.to_string()));
                        }
                    }
                    _ => {}
                }
            }
        }
        "user" if !subagent => {
            let results: Vec<&Value> = blocks(event)
                .iter()
                .filter(|block| text(block.get("type")) == Some("tool_result"))
                .collect();
            // The structured result is the hook's tool_response; it belongs
            // to the line's only result.
            let structured = event.get("tool_use_result").filter(|_| results.len() == 1);
            for block in results {
                let Some(id) = text(block.get("tool_use_id")) else {
                    continue;
                };
                let response = structured
                    .or_else(|| block.get("content"))
                    .cloned()
                    .unwrap_or(Value::Null);
                said.push(Said::Result {
                    id: id.to_string(),
                    response,
                    ok: block.get("is_error") != Some(&Value::Bool(true)),
                });
            }
        }
        "result" => {
            let failed = event.get("is_error") == Some(&Value::Bool(true))
                || text(event.get("subtype")) != Some("success");
            if failed {
                said.push(Said::Failed);
            } else {
                if let Some(answer) = text(event.get("result")) {
                    said.push(Said::Answer(answer.to_string()));
                }
                said.push(Said::Completed);
            }
        }
        _ => {}
    }
    said
}

/// The fields of `item` named in `names`, as one object.
fn fields(item: &Value, names: &[&str]) -> Value {
    let mut picked = Map::new();
    for name in names {
        if let Some(value) = item.get(*name) {
            picked.insert((*name).to_string(), value.clone());
        }
    }
    Value::Object(picked)
}

/// A completed Codex item that is a tool call: its name in Codex's own
/// vocabulary, its request and its result.
fn codex_tool(item: &Value) -> Option<Said> {
    let id = text(item.get("id")).map(str::to_string);
    let (name, input, response) = match text(item.get("type"))? {
        "command_execution" => (
            "command_execution".to_string(),
            fields(item, &["command"]),
            fields(item, &["aggregated_output", "exit_code", "status"]),
        ),
        "file_change" => (
            "file_change".to_string(),
            fields(item, &["changes"]),
            fields(item, &["status"]),
        ),
        "mcp_tool_call" => (
            format!(
                "mcp__{}__{}",
                text(item.get("server")).unwrap_or_default(),
                text(item.get("tool")).unwrap_or_default()
            ),
            item.get("arguments").cloned().unwrap_or(Value::Null),
            fields(item, &["result", "error", "status"]),
        ),
        "web_search" => (
            "web_search".to_string(),
            fields(item, &["query"]),
            Value::Null,
        ),
        _ => return None,
    };
    Some(Said::Tool {
        id,
        name,
        input,
        response,
    })
}

/// codex-cli 0.169.0's `exec --json` (record/x2b-captures-2026-11-21).
fn codex(event: &Map<String, Value>) -> Vec<Said> {
    match text(event.get("type")).unwrap_or_default() {
        "thread.started" => text(event.get("thread_id"))
            .map(|id| Said::Session(id.to_string()))
            .into_iter()
            .collect(),
        "item.completed" => {
            let Some(item) = event.get("item") else {
                return Vec::new();
            };
            if text(item.get("type")) == Some("agent_message") {
                return text(item.get("text"))
                    .filter(|words| !words.is_empty())
                    .map(|words| Said::Text(words.to_string()))
                    .into_iter()
                    .collect();
            }
            codex_tool(item).into_iter().collect()
        }
        "turn.completed" => vec![Said::Completed],
        "turn.failed" => vec![Said::Failed],
        _ => Vec::new(),
    }
}

/// OpenCode 2.3.2's `run --format json` (record/x2b-captures-2026-11-21).
fn opencode(event: &Map<String, Value>) -> Vec<Said> {
    let mut said = Vec::new();
    if let Some(session) = text(event.get("sessionID")) {
        said.push(Said::Session(session.to_string()));
    }
    let part = event.get("part");
    match text(event.get("type")).unwrap_or_default() {
        "step_start" => said.push(Said::Step),
        "text" => {
            if let Some(words) = text(part.and_then(|p| p.get("text"))).filter(|t| !t.is_empty()) {
                said.push(Said::Text(words.to_string()));
            }
        }
        "tool_use" => {
            let Some(part) = part else {
                return said;
            };
            let state = part.get("state");
            let response = match text(state.and_then(|s| s.get("status"))) {
                Some("completed") => state.and_then(|s| s.get("output")),
                Some("error") => state.and_then(|s| s.get("error")),
                _ => return said,
            };
            said.push(Said::Tool {
                id: text(part.get("callID"))
                    .or_else(|| text(part.get("id")))
                    .map(str::to_string),
                name: text(part.get("tool")).unwrap_or("tool").to_string(),
                input: state
                    .and_then(|s| s.get("input"))
                    .cloned()
                    .unwrap_or(json!({})),
                response: response.cloned().unwrap_or(Value::Null),
            });
        }
        "step_finish" if text(part.and_then(|p| p.get("reason"))) == Some("stop") => {
            said.push(Said::Completed);
        }
        "error" => said.push(Said::Failed),
        _ => {}
    }
    said
}

#[cfg(test)]
pub(super) mod tests;
