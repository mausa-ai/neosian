//! Claude Code's headless form, as 2.1.330 was observed (§40.50): `-p`
//! with stream-json, a session ID neosian names, presets through its own
//! permission modes, and `harness` denied under both server keys.

use serde_json::{Map, Value};

use super::adapter::{Mode, Plan, Request};
use super::events::{Event, head};

/// Both keys the server may be registered under (X2a may rename it).
pub const DENIED: [&str; 2] = ["mcp__neosian-memory__harness", "mcp__neosian__harness"];

pub fn plan(request: &Request) -> Plan {
    let mut argv: Vec<String> = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    .map(String::from)
    .to_vec();
    match request.resume {
        Some(session) => argv.extend(["--resume".to_string(), session.to_string()]),
        None => argv.extend(["--session-id".to_string(), request.new_session.to_string()]),
    }
    let mode: &[&str] = match request.mode {
        Mode::ReadOnly => &["--permission-mode", "dontAsk", "--tools", "Read,Grep,Glob"],
        Mode::Write => &[
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
        ],
    };
    argv.extend(mode.iter().map(|word| word.to_string()));
    if let Some(model) = request.model {
        argv.extend(["--model".to_string(), model.to_string()]);
    }
    // Variadic, so it comes last; no positional prompt follows.
    argv.push("--disallowedTools".to_string());
    argv.extend(DENIED.map(String::from));
    Plan {
        argv,
        env: Vec::new(),
    }
}

fn text(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str)
}

pub fn read(event: &Map<String, Value>) -> Vec<Event> {
    let mut events = Vec::new();
    match text(event.get("type")).unwrap_or_default() {
        "system" if text(event.get("subtype")) == Some("init") => {
            if let Some(session) = text(event.get("session_id")) {
                events.push(Event::Session(session.to_string()));
            }
        }
        "assistant" => {
            let blocks = event
                .get("message")
                .and_then(|message| message.get("content"))
                .and_then(Value::as_array);
            for block in blocks.into_iter().flatten() {
                match text(block.get("type")) {
                    Some("text") => {
                        if let Some(said) = text(block.get("text")).filter(|t| !t.is_empty()) {
                            events.push(Event::Text(said.to_string()));
                        }
                    }
                    Some("tool_use") => {
                        let name = text(block.get("name")).unwrap_or("?");
                        events.push(Event::Note(format!("[tool {name}]")));
                    }
                    _ => {}
                }
            }
        }
        "user" => {
            let blocks = event
                .get("message")
                .and_then(|message| message.get("content"))
                .and_then(Value::as_array);
            for block in blocks.into_iter().flatten() {
                if block.get("is_error") == Some(&Value::Bool(true)) {
                    let said = match block.get("content") {
                        Some(Value::String(said)) => said.clone(),
                        Some(other) => other.to_string(),
                        None => String::new(),
                    };
                    events.push(Event::Note(format!("[tool error] {}", head(&said, 400))));
                }
            }
        }
        "result" => {
            for denial in event
                .get("permission_denials")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let name = text(denial.get("tool_name")).unwrap_or("?");
                events.push(Event::Note(format!("[denied {name}]")));
            }
            let answer = text(event.get("result"));
            let failed = event.get("is_error") == Some(&Value::Bool(true))
                || text(event.get("subtype")).is_some_and(|s| s.starts_with("error"));
            if failed {
                let subtype = text(event.get("subtype")).unwrap_or("error");
                events.push(Event::Failure(answer.unwrap_or(subtype).to_string()));
            } else if let Some(answer) = answer {
                events.push(Event::Answer(answer.to_string()));
            }
        }
        _ => {}
    }
    events
}
