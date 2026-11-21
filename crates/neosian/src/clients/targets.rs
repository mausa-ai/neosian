//! The clients' paths (`mcp/targets.py`, `record/targets.py`) and how a
//! registration is read (DESIGN §40.53).

use super::{
    context::Context,
    files,
    naming::{self, NEW, OLD},
    receipt,
};
use serde_json::Value;
use std::path::PathBuf;

#[derive(Clone)]
pub struct Target {
    pub client: String,
    pub label: &'static str,
    pub level: String,
    pub path: PathBuf,
    pub evidence: PathBuf,
    /// Where rename runs are kept: a missing receipt is rebuilt from them.
    pub migrations: PathBuf,
    pub key: &'static str,
    pub cli: Option<&'static str>,
    pub plugin: bool,
    pub trust: Option<String>,
}

pub fn mcp(client: &str, context: &Context, level: &str) -> Target {
    let base = context.directory(client);
    let project = level == "project";
    let label = match client {
        "claude-code" => "Claude Code",
        "claude-desktop" => "Claude Desktop",
        "codex" => "Codex",
        "opencode" => "OpenCode",
        "muse-code" => "Muse Code",
        "cursor" => "Cursor",
        "pi" => "Pi",
        _ => unreachable!(),
    };
    let mut target = Target {
        client: client.into(),
        label,
        level: level.into(),
        path: base.clone(),
        evidence: base.clone(),
        migrations: super::rename::backup::migrations(context),
        key: "mcpServers",
        cli: None,
        plugin: false,
        trust: None,
    };
    target.path = match client {
        "claude-code" if project => context.cwd.join(".mcp.json"),
        "claude-code" => {
            target.cli = Some("claude");
            let overridden = context.directories.contains_key(client)
                || context
                    .env
                    .get("CLAUDE_CONFIG_DIR")
                    .is_some_and(|v| !v.is_empty());
            (if overridden { &base } else { &context.home }).join(".claude.json")
        }
        "codex" => {
            target.level = "user".into();
            target.key = "mcp_servers";
            target.cli = Some("codex");
            base.join("config.toml")
        }
        "claude-desktop" => {
            target.level = "user".into();
            base.join("claude_desktop_config.json")
        }
        "cursor" => {
            target.level = "user".into();
            base.join("mcp.json")
        }
        "opencode" => {
            target.key = "mcp";
            let at = if project { &context.cwd } else { &base };
            let commented = at.join("opencode.jsonc");
            if files::read(&commented).is_some() {
                commented
            } else {
                at.join("opencode.json")
            }
        }
        "muse-code" => {
            if project {
                context.cwd.join(".mcp.json")
            } else {
                base.join("settings.json")
            }
        }
        "pi" => (if project {
            context.cwd.join(".pi")
        } else {
            base
        })
        .join("mcp.json"),
        _ => unreachable!(),
    };
    target
}

pub fn hooks(client: &str, context: &Context, level: &str) -> Target {
    let mut target = mcp(client, context, level);
    let base = &target.evidence;
    let project = level == "project";
    target.level = level.into();
    target.cli = None;
    target.path = match client {
        "claude-code" => (if project {
            context.cwd.join(".claude")
        } else {
            base.clone()
        })
        .join("settings.json"),
        "codex" => {
            target.trust = Some(if project {
                format!(
                    "Codex loads project hooks only for a trusted project: [projects.\"{}\"] trust_level = \"trusted\" in its config.toml, or accept the trust prompt on first run",
                    context.cwd.display()
                )
            } else {
                "Codex runs a new hook once you have reviewed it: open /hooks in Codex and approve it".into()
            });
            (if project {
                context.cwd.join(".codex")
            } else {
                base.clone()
            })
            .join("hooks.json")
        }
        "opencode" => {
            target.plugin = true;
            (if project {
                context.cwd.join(".opencode")
            } else {
                base.clone()
            })
            .join("plugins/neosian-record.js")
        }
        "muse-code" => {
            if project {
                context.cwd.join(".muse/hooks.json")
            } else {
                base.join("settings.json")
            }
        }
        "cursor" => {
            target.trust =
                project.then(|| "Cursor loads project hooks only in a trusted workspace".into());
            (if project {
                context.cwd.join(".cursor")
            } else {
                base.clone()
            })
            .join("hooks.json")
        }
        "pi" => {
            target.plugin = true;
            target.trust = project.then(|| {
                "Pi loads project extensions and MCP servers only in a trusted project".into()
            });
            (if project {
                context.cwd.join(".pi")
            } else {
                base.clone()
            })
            .join("extensions/neosian-record.ts")
        }
        _ => unreachable!(),
    };
    target
}

/// The pointer of `key`'s entry in a target's document.
pub fn pointer(target: &Target, key: &str) -> String {
    super::rename::jsonedit::join(&[target.key, key])
}

/// A client's file as the client reads it (DESIGN §40.51 second amendment,
/// R8): the one reader of every path that reads or writes a registration.
pub enum Document {
    Absent,
    Read(Value),
    /// It exists and the client's own reading fails: never "absent".
    Unreadable,
}

impl Document {
    pub fn value(self) -> Option<Value> {
        match self {
            Self::Read(value) => Some(value),
            _ => None,
        }
    }
}

/// `path` read for `client`: a client this build renames is read as Claude
/// Code reads it (`rename::read`: a byte-order mark, lossy UTF-8, the last
/// duplicate key), and a file it cannot read is `Unreadable`; any other
/// client keeps its earlier reading, unchanged.
pub fn document(client: &str, path: &std::path::Path) -> Document {
    if !naming::renamed(client) {
        return files::read(path).map_or(Document::Absent, Document::Read);
    }
    if fs_absent(path) {
        return Document::Absent;
    }
    if path.extension().is_some_and(|e| e == "toml") {
        return files::read(path).map_or(Document::Unreadable, Document::Read);
    }
    match super::rename::read::file(path).and_then(|r| r.json()) {
        Some(value) => Document::Read(value),
        None => Document::Unreadable,
    }
}

fn fs_absent(path: &std::path::Path) -> bool {
    matches!(std::fs::symlink_metadata(path), Err(e) if e.kind() == std::io::ErrorKind::NotFound)
}

/// Whether the entry under `neosian` in this target's file is ours: only by
/// the receipt, and only for a client this build renames (DESIGN §40.51).
pub(super) fn ours_new(target: &Target, document: &Value) -> Option<bool> {
    let entry = document.get(target.key)?.get(NEW)?;
    Some(
        naming::renamed(&target.client)
            && receipt::owns_in(&book(target), &target.path, &pointer(target, NEW), entry),
    )
}

/// The receipt this target's ownership is read from.
pub fn book(target: &Target) -> receipt::Book {
    receipt::Book::at(&target.evidence, &target.migrations)
}

/// The key a registration is read under: `neosian` when ours, else the old
/// key, which is ours by name; and its command line.
pub fn registration(target: &Target) -> Option<(&'static str, Vec<String>)> {
    let document = document(&target.client, &target.path).value()?;
    if ours_new(target, &document) == Some(true) {
        return Some((NEW, argv(&document[target.key][NEW])?));
    }
    Some((OLD, argv(document.get(target.key)?.get(OLD)?)?))
}

pub fn registered(target: &Target) -> Option<Vec<String>> {
    registration(target).map(|(_, argv)| argv)
}

/// An entry's full command line (`argv[0]` is the executable).
pub fn argv(entry: &Value) -> Option<Vec<String>> {
    if let Some(parts) = entry.get("command")?.as_array() {
        return Some(parts.iter().map(crate::pyfmt::py_str).collect());
    }
    let mut argv = vec![entry.get("command")?.as_str()?.to_owned()];
    if let Some(args) = entry.get("args") {
        argv.extend(args.as_array()?.iter().map(crate::pyfmt::py_str));
    }
    Some(argv)
}

pub fn managed(document: &Value, settings: &std::path::Path) -> Option<PathBuf> {
    let named = document
        .get("managed_hooks_path")?
        .as_str()
        .filter(|s| !s.is_empty())?;
    Some(settings.parent()?.join(named))
}

pub fn active(client: &str, context: &Context, level: &str) -> Vec<Target> {
    let target = hooks(client, context, level);
    if client == "muse-code"
        && level == "user"
        && let Some(document) = files::read(&target.path)
        && let Some(path) = managed(&document, &target.path)
        && path != target.path
    {
        let mut other = target.clone();
        other.path = path;
        return vec![other, target];
    }
    vec![target]
}
