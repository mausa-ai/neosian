//! Where each client keeps its MCP registrations, every scope it reads
//! (DESIGN §40.53 item 1): user level, Claude Code's local scope, and the
//! projects the client's own index records, with the current directory's
//! project files always read.

use super::{claude_cli::Scope, jsonedit, read};
use crate::clients::{
    context::Context,
    files,
    naming::{NEW, OLD},
    targets,
};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    /// `.claude.json`, moved through Claude Code's CLI.
    Claude(Scope),
    /// A JSON file whose servers map is this member.
    Json(&'static str),
    /// A Codex `config.toml` (`mcp_servers`).
    Toml,
}

#[derive(Clone, Debug)]
pub struct Container {
    pub client: &'static str,
    pub file: PathBuf,
    pub kind: Kind,
    /// The project it serves; `None` at user level (every project).
    pub project: Option<PathBuf>,
}

/// What a container holds under the two keys.
pub struct Entries {
    pub old: Option<Value>,
    pub new: Option<Value>,
}

impl Container {
    pub fn pointer(&self, key: &str) -> String {
        match &self.kind {
            Kind::Claude(scope) => scope.pointer(key),
            Kind::Json(member) => jsonedit::join(&[member, key]),
            Kind::Toml => jsonedit::join(&["mcp_servers", key]),
        }
    }

    /// Whether this file is read by more than one client (a project
    /// `.mcp.json`: Claude Code and Muse Code).
    pub fn shared(&self) -> bool {
        self.file.file_name().is_some_and(|n| n == ".mcp.json")
    }

    /// The entries under both keys, read as the client reads the file;
    /// `Err(())` when it exists and cannot be read.
    pub fn entries(&self) -> Result<Entries, ()> {
        let document = match targets::document(self.client, &self.file) {
            targets::Document::Absent => json!({}),
            targets::Document::Read(value) => value,
            targets::Document::Unreadable => return Err(()),
        };
        let at = |key: &str| document.pointer(&self.pointer(key)).cloned();
        Ok(Entries {
            old: at(OLD),
            new: at(NEW),
        })
    }
}

fn json(path: &Path) -> Value {
    read::file(path)
        .and_then(|r| r.json())
        .unwrap_or(Value::Null)
}

fn keys(value: &Value) -> Vec<PathBuf> {
    value
        .as_object()
        .into_iter()
        .flatten()
        .map(|(k, _)| PathBuf::from(k))
        .collect()
}

/// The projects a client's own index records, and the current directory.
pub fn projects(client: &str, context: &Context) -> Vec<PathBuf> {
    let base = context.directory(client);
    let mut found = match client {
        "claude-code" => keys(&json(&targets::mcp(client, context, "user").path)["projects"]),
        "codex" => keys(&files::read(&base.join("config.toml")).unwrap_or_default()["projects"]),
        "pi" => json(&base.join("trust.json"))
            .as_object()
            .into_iter()
            .flatten()
            .filter(|(_, trusted)| trusted.as_bool() == Some(true))
            .map(|(k, _)| PathBuf::from(k))
            .collect(),
        "muse-code" => keys(&json(&base.join("trust.json"))),
        _ => vec![],
    };
    found.push(context.cwd.clone());
    let mut seen = std::collections::BTreeSet::new();
    found
        .into_iter()
        .filter_map(|p| p.canonicalize().ok())
        .filter(|p| p.is_dir() && seen.insert(p.clone()))
        .collect()
}

/// Every container the client reads, user level first.
pub fn containers(client: &'static str, context: &Context) -> Vec<Container> {
    let user = targets::mcp(client, context, "user").path;
    let at = |file: PathBuf, kind: Kind, project: Option<PathBuf>| Container {
        client,
        file,
        kind,
        project,
    };
    let mut found = vec![];
    let projects = projects(client, context);
    match client {
        "claude-code" => {
            found.push(at(user.clone(), Kind::Claude(Scope::User), None));
            for (key, entry) in json(&user)["projects"].as_object().into_iter().flatten() {
                if entry["mcpServers"]
                    .as_object()
                    .is_some_and(|m| !m.is_empty())
                {
                    let scope = Scope::Local(key.clone());
                    found.push(at(
                        user.clone(),
                        Kind::Claude(scope),
                        Some(PathBuf::from(key)),
                    ));
                }
            }
            for p in &projects {
                found.push(at(
                    p.join(".mcp.json"),
                    Kind::Json("mcpServers"),
                    Some(p.clone()),
                ));
            }
        }
        "codex" => {
            found.push(at(user, Kind::Toml, None));
            for p in &projects {
                found.push(at(
                    p.join(".codex/config.toml"),
                    Kind::Toml,
                    Some(p.clone()),
                ));
            }
        }
        "opencode" => {
            // OpenCode reads both files in a directory, the `.jsonc` last.
            let base = context.directory(client);
            for (dir, project) in [
                (base, None),
                (context.cwd.clone(), Some(context.cwd.clone())),
            ] {
                for name in ["opencode.json", "opencode.jsonc"] {
                    let file = dir.join(name);
                    if file.exists() || (name == "opencode.json" && project.is_none()) {
                        found.push(at(file, Kind::Json("mcp"), project.clone()));
                    }
                }
            }
            let _ = user;
        }
        "muse-code" | "pi" => {
            found.push(at(user, Kind::Json("mcpServers"), None));
            for p in &projects {
                let file = if client == "pi" {
                    p.join(".pi/mcp.json")
                } else {
                    p.join(".mcp.json")
                };
                found.push(at(file, Kind::Json("mcpServers"), Some(p.clone())));
            }
        }
        _ => {
            found.push(at(user, Kind::Json("mcpServers"), None));
            if client == "cursor" {
                let project = context.cwd.join(".cursor/mcp.json");
                found.push(at(
                    project,
                    Kind::Json("mcpServers"),
                    Some(context.cwd.clone()),
                ));
            }
        }
    }
    // A file read in two roles (the user file is this directory's project
    // file when neosian runs in the home folder) is one container.
    let mut seen: Vec<(PathBuf, String)> = vec![];
    found.retain(|c| {
        let key = (
            c.file.canonicalize().unwrap_or_else(|_| c.file.clone()),
            c.pointer(OLD),
        );
        let fresh = !seen.contains(&key);
        seen.push(key);
        fresh
    });
    found
}

/// Whether a file neosian cannot read names one of the two keys, so the
/// client may hold an entry of ours (or another server's `neosian`) there.
pub fn names_a_key(path: &Path) -> bool {
    std::fs::read(path).is_ok_and(|bytes| {
        let text = String::from_utf8_lossy(&bytes).to_lowercase();
        text.contains("neosian-memory") || text.contains("\"neosian\"")
    })
}
