//! Codex's headless form, as 0.169.0 was observed (§40.50): `exec --json`
//! with approval never (without it the read-only sandbox did not hold),
//! the sandbox as the preset (off under neosian's common policy on macOS,
//! X4b), and the child denied `harness` through TMPDIR's marker and, per
//! registered key, the fields its transport accepts (`transport.rs`, X4c).
//! In `read-only` (#431) every other configured MCP server is switched off
//! for the run and neosian's stdio entry runs this binary with read-only
//! arguments (#432).

use serde_json::{Map, Value, json};

use super::adapter::{CodexKey, Mode, Plan, Request};
use super::events::{ERROR_BYTES, Event, head, observed};
use super::{signin, transport};

/// The servers `codex mcp list --json` reported: each one's name, or, for
/// an entry without a string name, its position in the list (from 1), which
/// no override can address (#432). The entry itself is never repeated: it
/// may carry a server's environment.
pub fn server_names(answer: &str) -> Option<Vec<Result<String, usize>>> {
    let Ok(Value::Array(servers)) = serde_json::from_str::<Value>(answer) else {
        return None;
    };
    let listed = |(at, server): (usize, &Value)| match server.get("name").and_then(Value::as_str) {
        Some(name) => Ok(name.to_string()),
        None => Err(at + 1),
    };
    Some(servers.iter().enumerate().map(listed).collect())
}

/// Codex's own switch for running without its sandbox and approvals. Only
/// a launch that neosian's common policy confines uses it: Codex's sandbox
/// cannot start inside another (macOS refuses a second profile, so every
/// command failed), and the outer policy is then Codex's boundary (X4b).
pub const OUTER_POLICY: &str = "--dangerously-bypass-approvals-and-sandbox";

pub fn plan(request: &Request) -> Plan {
    let mut argv: Vec<String> = ["codex", "exec", "--json"].map(String::from).to_vec();
    let mode: &[&str] = match (request.confined, request.mode) {
        (true, Mode::ReadOnly) => &["--skip-git-repo-check", OUTER_POLICY],
        (true, Mode::Write) => &[OUTER_POLICY],
        (false, Mode::ReadOnly) => &[
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "-c",
            "approval_policy=\"never\"",
        ],
        (false, Mode::Write) => &[
            "-s",
            "workspace-write",
            "-c",
            "approval_policy=\"never\"",
            "-c",
            "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c",
            "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        ],
    };
    argv.extend(mode.iter().map(|word| word.to_string()));
    argv.extend(["-C".to_string(), request.directory.display().to_string()]);
    if let Some(model) = request.model {
        argv.extend(["-m".to_string(), model.to_string()]);
    }
    let read_only = request.server.filter(|_| request.mode == Mode::ReadOnly);
    if read_only.is_some() {
        // Every other configured server is off for this run, and the apps
        // Codex serves from the account as `codex_apps` too (#431).
        argv.extend(["--disable".to_string(), "apps".to_string()]);
        for name in request.codex_off {
            argv.extend([
                "-c".to_string(),
                format!("mcp_servers.{name}.enabled=false"),
            ]);
        }
    }
    for found in request.codex_keys {
        for field in transport::fields(found, read_only) {
            argv.extend(["-c".to_string(), field]);
        }
    }
    if let Some(thread) = request.resume {
        argv.extend(["resume".to_string(), thread.to_string()]);
    }
    argv.push("-".to_string());
    Plan {
        argv,
        env: vec![("TMPDIR".to_string(), request.private.display().to_string())],
    }
}

/// The keys found, for a plan: `found` pairs a key with its answer. An
/// answer that is not a JSON object is skipped; Err names the key and a
/// transport neosian does not know (X4c).
pub fn keys(found: &[(&'static str, String)]) -> Result<Vec<CodexKey>, (String, String)> {
    let mut keys = Vec::new();
    for (key, answer) in found {
        match transport::key(key, answer) {
            Some(Ok(entry)) => keys.push(entry),
            Some(Err(unknown)) => return Err((key.to_string(), unknown)),
            None => {}
        }
    }
    Ok(keys)
}

fn text(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str)
}

/// One command, started or completed: the item's `id` names it in both
/// entries, so a completion reads as the same command (#432).
fn command(item: &Value, kind: &str) -> Event {
    let mut fields = Vec::new();
    if let Some(id) = text(item.get("id")) {
        fields.push(("id", json!(id)));
    }
    let command = text(item.get("command")).unwrap_or_default();
    fields.push(("command", json!(command)));
    if kind == "command" {
        fields.push((
            "exit",
            item.get("exit_code").cloned().unwrap_or(Value::Null),
        ));
    }
    observed(kind, &fields)
}

pub fn read(event: &Map<String, Value>) -> Vec<Event> {
    match text(event.get("type")).unwrap_or_default() {
        "thread.started" => text(event.get("thread_id"))
            .map(|id| Event::Session(id.to_string()))
            .into_iter()
            .collect(),
        // A running command is seen before it completes (x2c r1).
        "item.started" => event
            .get("item")
            .filter(|item| text(item.get("type")) == Some("command_execution"))
            .map(|item| command(item, "command_started"))
            .into_iter()
            .collect(),
        "item.completed" => {
            let Some(item) = event.get("item") else {
                return Vec::new();
            };
            match text(item.get("type")).unwrap_or_default() {
                "agent_message" => text(item.get("text"))
                    .filter(|said| !said.is_empty())
                    .map(|said| Event::Text(said.to_string()))
                    .into_iter()
                    .collect(),
                "command_execution" => vec![command(item, "command")],
                "reasoning" => Vec::new(),
                "error" => {
                    let message = text(item.get("message")).unwrap_or_default();
                    vec![observed("error", &[("text", json!(message))])]
                }
                other => {
                    let status = item.get("status").cloned().unwrap_or(Value::Null);
                    vec![observed(
                        "item",
                        &[("type", json!(other)), ("status", status)],
                    )]
                }
            }
        }
        "turn.failed" => {
            let message = event
                .get("error")
                .and_then(|error| text(error.get("message")))
                .unwrap_or("turn.failed");
            // Codex's own words telling the person to sign in again (X4c).
            if signin::codex_said(message) {
                return vec![signin::failed(super::adapter::Agent::Codex)];
            }
            vec![Event::Failure(head(message, ERROR_BYTES).to_string())]
        }
        // Codex also reports retries this way; only the exit and
        // `turn.failed` decide the turn.
        "error" => {
            let message = text(event.get("message")).unwrap_or_default();
            vec![observed("error", &[("text", json!(message))])]
        }
        _ => Vec::new(),
    }
}
