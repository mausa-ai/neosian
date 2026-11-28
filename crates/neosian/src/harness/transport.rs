//! neosian's Codex registrations by transport (X4c, closing what §40.50
//! #432 item 27 left open). Codex takes a field of an MCP entry only for the
//! transport it belongs to: `env`, `args` and `command` are stdio fields,
//! and an HTTP (`streamable_http`) entry refuses them (codex-cli 0.169.0:
//! "env is not supported for streamable_http"), while `enabled`,
//! `enabled_tools` and `disabled_tools` belong to every entry. So each entry
//! is classified first and given only its own transport's fields, and a
//! transport neosian does not know is refused by name, never guessed.
//!
//! | entry | `write` | `read-only` |
//! |---|---|---|
//! | `neosian-memory` or `neosian`, stdio | `env` marker, `disabled_tools` | the same, plus this binary as `command`, read-only `args` (and `enabled_tools` under a profile) |
//! | `neosian-cloud`, stdio | `env` marker, `disabled_tools` | `enabled=false` |
//! | any of the three, `streamable_http` | `disabled_tools` | `enabled=false` |
//!
//! An HTTP entry is never this server and cannot be given this binary or
//! read-only arguments, and the hosted entry (#433) reaches another store,
//! so in `read-only` both are switched off like every other server. The
//! `harness` tool is never served over HTTP; `disabled_tools` names it
//! there too, as a second layer.

use serde_json::Value;

use super::adapter::{CodexKey, McpServer, READ_ONLY_TOOLS};

/// The keys this server is registered under, the old name and the current.
pub const OWN: [&str; 2] = ["neosian-memory", "neosian"];
/// Every key probed with `codex mcp get`: ours and the hosted entry's.
pub const KEYS: [&str; 3] = ["neosian-memory", "neosian", "neosian-cloud"];

/// A transport neosian knows how to restrict.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Transport {
    Stdio,
    Http,
}

/// The longest transport name a refusal repeats.
const NAME_CHARS: usize = 40;

/// An entry's transport, from `transport.type` in an entry of `codex mcp
/// get --json` or `codex mcp list --json`. Err holds the name of one
/// neosian does not know, repeated only when it reads as a name: an entry
/// may carry headers or an environment, which are never repeated.
pub fn transport(entry: &Value) -> Result<Transport, String> {
    match entry
        .get("transport")
        .and_then(|transport| transport.get("type"))
    {
        None | Some(Value::Null) => Err("missing".to_string()),
        Some(Value::String(name)) => match name.as_str() {
            "stdio" => Ok(Transport::Stdio),
            "streamable_http" => Ok(Transport::Http),
            other if named(other) => Err(other.to_string()),
            _ => Err("unreadable".to_string()),
        },
        Some(_) => Err("unreadable".to_string()),
    }
}

fn named(name: &str) -> bool {
    (1..=NAME_CHARS).contains(&name.len())
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_-.".contains(c))
}

/// The transports `codex mcp list --json` reported, by position.
pub fn listed(answer: &str) -> Vec<Result<Transport, String>> {
    match serde_json::from_str::<Value>(answer) {
        Ok(Value::Array(servers)) => servers.iter().map(transport).collect(),
        _ => Vec::new(),
    }
}

/// One key's `codex mcp get KEY --json` answer: None when it is not a JSON
/// object (the key gets no override, so it stays covered by TMPDIR's marker
/// and, in `read-only`, by the list), Err with the transport's name when
/// neosian does not know it.
pub fn key(key: &str, answer: &str) -> Option<Result<CodexKey, String>> {
    let Ok(entry @ Value::Object(_)) = serde_json::from_str::<Value>(answer) else {
        return None;
    };
    let tools = |name: &str| match entry.get(name) {
        Some(Value::Array(tools)) => Some(
            tools
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<Vec<_>>(),
        ),
        _ => None,
    };
    Some(transport(&entry).map(|transport| CodexKey {
        key: key.to_string(),
        disabled: tools("disabled_tools").unwrap_or_default(),
        enabled: tools("enabled_tools"),
        transport,
    }))
}

/// A TOML basic string (JSON's escapes are TOML's for these characters).
fn toml_string(text: &str) -> String {
    Value::String(text.to_string()).to_string()
}

/// A TOML inline array of basic strings.
fn toml_list(items: &[String]) -> String {
    let quoted: Vec<String> = items.iter().map(|item| toml_string(item)).collect();
    format!("[{}]", quoted.join(","))
}

/// The `-c` overrides for one key Codex has, by the table above; `server`
/// is this neosian's own server when the child is `read-only`.
pub fn fields(found: &CodexKey, server: Option<&McpServer>) -> Vec<String> {
    let key = found.key.as_str();
    let this_server = OWN.contains(&key) && found.transport == Transport::Stdio;
    if server.is_some() && !this_server {
        return vec![format!("mcp_servers.{key}.enabled=false")];
    }
    let mut disabled = found.disabled.clone();
    if !disabled.iter().any(|tool| tool == "harness") {
        disabled.push("harness".to_string());
    }
    let mut fields = Vec::new();
    if found.transport == Transport::Stdio {
        fields.push(format!("mcp_servers.{key}.env.{}=\"1\"", super::CHILD_ENV));
    }
    fields.push(format!(
        "mcp_servers.{key}.disabled_tools={}",
        toml_list(&disabled)
    ));
    if let Some(server) = server {
        // This binary, never a wrapper the entry may name, started with
        // every mount read-only; under a profile, only the tools that
        // cannot change state (#431, #432).
        fields.push(format!(
            "mcp_servers.{key}.command={}",
            toml_string(&server.command)
        ));
        fields.push(format!(
            "mcp_servers.{key}.args={}",
            toml_list(&server.args)
        ));
        if !server.mounted {
            // Within what the entry already allows, never wider (X4f Q8).
            let allowed: Vec<String> = READ_ONLY_TOOLS
                .iter()
                .filter(|tool| {
                    found
                        .enabled
                        .as_ref()
                        .is_none_or(|kept| kept.iter().any(|k| k == *tool))
                })
                .map(|tool| tool.to_string())
                .collect();
            fields.push(format!(
                "mcp_servers.{key}.enabled_tools={}",
                toml_list(&allowed)
            ));
        }
    }
    fields
}

#[cfg(test)]
mod tests;
