//! Codex's headless form, as 0.169.0 was observed (§40.50): `exec --json`
//! with approval never (without it the read-only sandbox did not hold),
//! the sandbox as the preset, and the child denied `harness` through
//! TMPDIR's marker and, per registered key, an env member and the key's
//! own `disabled_tools` with `harness` added. In `read-only` (#431) every
//! other configured MCP server is switched off for the run and neosian's
//! entry is given read-only arguments.

use serde_json::{Map, Value, json};

use super::adapter::{CodexKey, Mode, Plan, READ_ONLY_TOOLS, Request};
use super::events::{ERROR_BYTES, Event, head, observed};

/// The keys the server may be registered under, probed with `codex mcp get`.
pub const KEYS: [&str; 2] = ["neosian-memory", "neosian"];

/// A TOML basic string (JSON's escapes are TOML's for these characters).
fn toml_string(text: &str) -> String {
    Value::String(text.to_string()).to_string()
}

/// A TOML inline array of basic strings.
fn toml_list(items: &[String]) -> String {
    let quoted: Vec<String> = items.iter().map(|item| toml_string(item)).collect();
    format!("[{}]", quoted.join(","))
}

/// The server names `codex mcp list --json` reported.
pub fn server_names(answer: &str) -> Option<Vec<String>> {
    let Ok(Value::Array(servers)) = serde_json::from_str::<Value>(answer) else {
        return None;
    };
    Some(
        servers
            .iter()
            .filter_map(|server| server.get("name").and_then(Value::as_str))
            .map(str::to_string)
            .collect(),
    )
}

pub fn plan(request: &Request) -> Plan {
    let mut argv: Vec<String> = ["codex", "exec", "--json"].map(String::from).to_vec();
    let mode: &[&str] = match request.mode {
        Mode::ReadOnly => &[
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "-c",
            "approval_policy=\"never\"",
        ],
        Mode::Write => &[
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
        let key = found.key;
        let mut disabled: Vec<String> = found.disabled.clone();
        if !disabled.iter().any(|tool| tool == "harness") {
            disabled.push("harness".to_string());
        }
        argv.extend([
            "-c".to_string(),
            format!("mcp_servers.{key}.env.{}=\"1\"", super::CHILD_ENV),
            "-c".to_string(),
            format!("mcp_servers.{key}.disabled_tools={}", toml_list(&disabled)),
        ]);
        if let Some(server) = read_only {
            // The entry's own command, started with every mount read-only;
            // under a profile, only the tools that cannot change state.
            argv.extend([
                "-c".to_string(),
                format!("mcp_servers.{key}.args={}", toml_list(&server.args)),
            ]);
            if !server.mounted {
                let allowed: Vec<String> = READ_ONLY_TOOLS.map(String::from).to_vec();
                argv.extend([
                    "-c".to_string(),
                    format!("mcp_servers.{key}.enabled_tools={}", toml_list(&allowed)),
                ]);
            }
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

/// What `codex mcp get KEY --json` said: the key's disabled tools.
pub fn disabled_tools(answer: &str) -> Option<Vec<String>> {
    let Ok(Value::Object(entry)) = serde_json::from_str::<Value>(answer) else {
        return None;
    };
    Some(match entry.get("disabled_tools") {
        Some(Value::Array(tools)) => tools
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect(),
        _ => Vec::new(),
    })
}

/// The keys found, for a plan: `found` pairs a key with its answer.
pub fn keys(found: &[(&'static str, String)]) -> Vec<CodexKey> {
    found
        .iter()
        .filter_map(|(key, answer)| {
            disabled_tools(answer).map(|disabled| CodexKey { key, disabled })
        })
        .collect()
}

fn text(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str)
}

pub fn read(event: &Map<String, Value>) -> Vec<Event> {
    match text(event.get("type")).unwrap_or_default() {
        "thread.started" => text(event.get("thread_id"))
            .map(|id| Event::Session(id.to_string()))
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
                "command_execution" => {
                    let command = text(item.get("command")).unwrap_or_default();
                    let exit = item.get("exit_code").cloned().unwrap_or(Value::Null);
                    vec![observed(
                        "command",
                        &[("command", json!(command)), ("exit", exit)],
                    )]
                }
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
