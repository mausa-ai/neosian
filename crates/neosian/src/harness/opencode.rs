//! OpenCode's headless form, as 2.3.2 was observed (§40.50): `run
//! --standalone` (the background service sees neither the child's
//! environment nor its config), the message on standard input, and the
//! preset as one of two agents carried in `OPENCODE_CONFIG_CONTENT`.

use serde_json::{Map, Value, json};

use super::adapter::{Mode, Plan, Request, Unplanned};
use super::events::{Event, head};

pub const CONFIG_ENV: &str = "OPENCODE_CONFIG_CONTENT";
/// Both keys the server may be registered under; OpenCode names MCP tools
/// `<key>_<tool>` with the key verbatim.
pub const DENIED: [&str; 2] = ["neosian-memory_harness", "neosian_harness"];

pub fn agent_name(mode: Mode) -> &'static str {
    match mode {
        Mode::ReadOnly => "neosian-harness-read-only",
        Mode::Write => "neosian-harness-write",
    }
}

/// One agent's permission rules, in the order OpenCode applies them.
fn permission(mode: Mode) -> Value {
    let mut rules = Map::new();
    rules.insert("*".into(), json!("deny"));
    rules.insert(
        "read".into(),
        json!({"*": "allow", "*.env": "deny", "*.env.*": "deny", "*.env.example": "allow"}),
    );
    for tool in ["glob", "grep", "list"] {
        rules.insert(tool.into(), json!("allow"));
    }
    if mode == Mode::Write {
        rules.insert("edit".into(), json!("allow"));
        rules.insert("external_directory".into(), json!("deny"));
    }
    for tool in DENIED {
        rules.insert(tool.into(), json!("deny"));
    }
    Value::Object(rules)
}

/// The config content with both agents added to what the server carries.
pub fn config(existing: Option<&str>) -> Result<String, Unplanned> {
    let mut document = match existing.filter(|text| !text.trim().is_empty()) {
        None => Map::new(),
        Some(text) => match serde_json::from_str::<Value>(text) {
            Ok(Value::Object(document)) => document,
            _ => return Err(Unplanned::OpenCodeConfig),
        },
    };
    let agents = document
        .entry("agent")
        .or_insert_with(|| Value::Object(Map::new()));
    let Value::Object(agents) = agents else {
        return Err(Unplanned::OpenCodeConfig);
    };
    for mode in [Mode::ReadOnly, Mode::Write] {
        agents.insert(
            agent_name(mode).to_string(),
            json!({
                "mode": "primary",
                "description": super::text::say("opencode_agent", &[("mode", mode.name())]),
                "permission": permission(mode),
            }),
        );
    }
    Ok(Value::Object(document).to_string())
}

pub fn plan(request: &Request) -> Result<Plan, Unplanned> {
    let mut argv: Vec<String> = [
        "opencode",
        "run",
        "--standalone",
        "--format",
        "json",
        "--agent",
    ]
    .map(String::from)
    .to_vec();
    argv.push(agent_name(request.mode).to_string());
    if let Some(model) = request.model {
        argv.extend(["--model".to_string(), model.to_string()]);
    }
    if let Some(session) = request.resume {
        argv.extend(["--session".to_string(), session.to_string()]);
    }
    Ok(Plan {
        argv,
        env: vec![(CONFIG_ENV.to_string(), config(request.opencode_config)?)],
    })
}

fn text(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str)
}

pub fn read(event: &Map<String, Value>) -> Vec<Event> {
    let mut events = Vec::new();
    if let Some(session) = text(event.get("sessionID")) {
        events.push(Event::Session(session.to_string()));
    }
    let part = event.get("part");
    match text(event.get("type")).unwrap_or_default() {
        "text" => {
            if let Some(said) = text(part.and_then(|p| p.get("text"))).filter(|t| !t.is_empty()) {
                events.push(Event::Text(said.to_string()));
            }
        }
        "tool_use" => {
            let tool = text(part.and_then(|p| p.get("tool"))).unwrap_or("?");
            let state = part.and_then(|p| p.get("state"));
            let status = text(state.and_then(|s| s.get("status"))).unwrap_or("");
            let line = match text(state.and_then(|s| s.get("error"))) {
                Some(error) => format!("[tool {tool}] {status}: {}", head(error, 400)),
                None => format!("[tool {tool}] {status}"),
            };
            events.push(Event::Note(line.trim_end().to_string()));
        }
        "error" => {
            let error = event.get("error");
            let message = text(error.and_then(|e| e.get("message")))
                .or_else(|| text(error.and_then(|e| e.get("type"))))
                .unwrap_or("error");
            events.push(Event::Failure(message.to_string()));
        }
        _ => {}
    }
    events
}
