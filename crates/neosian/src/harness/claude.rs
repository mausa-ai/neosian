//! Claude Code's headless form, as 2.1.330 was observed (§40.50, amended
//! by #430): `-p` with stream-json and a session ID neosian names. In
//! `read-only` the child loads no settings but the person's own user
//! settings (no project or local hooks or allow rules) and one MCP server,
//! this neosian, whose tools it may use; `harness` is denied under both
//! server keys. `write` keeps the person's normal configuration.

use serde_json::{Map, Value, json};

use super::adapter::{Mode, Plan, Request, SERVER_KEY};
use super::events::{ERROR_BYTES, Event, head, observed};

/// Both keys the server may be registered under (X2a may rename it).
pub const DENIED: [&str; 2] = ["mcp__neosian-memory__harness", "mcp__neosian__harness"];

fn words(items: &[&str]) -> Vec<String> {
    items.iter().map(|word| word.to_string()).collect()
}

pub fn plan(request: &Request) -> Plan {
    let mut argv = words(&[
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
    ]);
    match request.resume {
        Some(session) => argv.extend(words(&["--resume", session])),
        None => argv.extend(words(&["--session-id", request.new_session])),
    }
    match request.mode {
        Mode::ReadOnly => {
            argv.extend(words(&["--setting-sources", "user", "--strict-mcp-config"]));
            if let Some(server) = request.server {
                let config = json!({"mcpServers": {SERVER_KEY: {
                    "command": server.command,
                    "args": server.args,
                }}});
                argv.extend(["--mcp-config".to_string(), config.to_string()]);
            }
            argv.extend(words(&[
                "--permission-mode",
                "dontAsk",
                "--tools",
                "Read,Grep,Glob",
            ]));
        }
        Mode::Write => argv.extend(words(&[
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
        ])),
    }
    if let Some(model) = request.model {
        argv.extend(words(&["--model", model]));
    }
    if request.mode == Mode::ReadOnly && request.server.is_some() {
        argv.extend(["--allowedTools".to_string(), format!("mcp__{SERVER_KEY}")]);
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

fn blocks(event: &Map<String, Value>) -> impl Iterator<Item = &Value> {
    event
        .get("message")
        .and_then(|message| message.get("content"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
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
            for block in blocks(event) {
                match text(block.get("type")) {
                    Some("text") => {
                        if let Some(said) = text(block.get("text")).filter(|t| !t.is_empty()) {
                            events.push(Event::Text(said.to_string()));
                        }
                    }
                    Some("tool_use") => {
                        let name = text(block.get("name")).unwrap_or_default();
                        events.push(observed("tool", &[("name", json!(name))]));
                    }
                    _ => {}
                }
            }
        }
        "user" => {
            for block in blocks(event) {
                if block.get("is_error") == Some(&Value::Bool(true)) {
                    let said = match block.get("content") {
                        Some(Value::String(said)) => said.clone(),
                        Some(other) => other.to_string(),
                        None => String::new(),
                    };
                    events.push(observed("tool_error", &[("text", json!(said))]));
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
                let name = text(denial.get("tool_name")).unwrap_or_default();
                events.push(observed("denied", &[("name", json!(name))]));
            }
            let answer = text(event.get("result"));
            let failed = event.get("is_error") == Some(&Value::Bool(true))
                || text(event.get("subtype")).is_some_and(|s| s.starts_with("error"));
            if failed {
                let subtype = text(event.get("subtype")).unwrap_or("error");
                let failure = head(answer.unwrap_or(subtype), ERROR_BYTES);
                events.push(Event::Failure(failure.to_string()));
            } else if let Some(answer) = answer {
                events.push(Event::Answer(answer.to_string()));
            }
        }
        _ => {}
    }
    events
}
