//! The mention search (DESIGN §40.53 item 3), a courtesy stated as such:
//! `neosian-memory` or `mcp__neosian` in any case in the client's own
//! configuration and in the frontmatter of its agents, skills and commands,
//! at user level and in each visited project. What neosian installed is passed over, and so are the
//! string entries of allow lists in plain JSON (reported, never blocking).
//! No evaluation, no YAML, no plugins, no prose bodies.

use super::{jsonedit, read, search_files::files, toml_key};
use crate::clients::{
    context::Context,
    hooks,
    naming::{NEW, OLD},
};
use serde_json::Value;
use std::path::{Path, PathBuf};

/// One place found: its file and line; an allow entry does not block.
#[derive(Clone, Debug)]
pub struct Mention {
    pub path: PathBuf,
    pub line: usize,
    pub allow: bool,
}

/// The search's result for one client; `unreadable` files block too.
#[derive(Default)]
pub struct Found {
    pub mentions: Vec<Mention>,
    pub unreadable: Vec<PathBuf>,
}

impl Found {
    pub fn blocks(&self) -> bool {
        !self.unreadable.is_empty() || self.mentions.iter().any(|m| !m.allow)
    }
}

#[derive(Clone, Copy)]
pub(super) enum How {
    /// A JSON file: every key and string, ours and allow entries passed over.
    Json,
    /// `.claude.json`: only its server maps and project server and tool lists.
    ClaudeState,
    /// A TOML file: every line but our tables and project headers.
    Toml,
    /// The frontmatter of a Markdown file.
    Frontmatter,
}

/// A mention refers to the server or its tools (N5 refined): it holds
/// `neosian-memory` or `mcp__neosian`, in any letter case; a path or a
/// command that merely contains the word is not one.
fn names(text: &str) -> bool {
    let lower = text.to_lowercase();
    lower.contains("neosian-memory") || lower.contains("mcp__neosian")
}

/// Keys that are paths by their position are never mentions: the projects
/// a client records.
fn path_key(segments: &[String]) -> bool {
    matches!(segments, [projects] if projects == "projects")
}

fn line_at(text: &str, offset: usize) -> usize {
    text[..offset.min(text.len())].matches('\n').count() + 1
}

/// Allow lists that do not block (string entries only).
fn allow(segments: &[String]) -> bool {
    let s: Vec<&str> = segments.iter().map(String::as_str).collect();
    matches!(
        s.as_slice(),
        ["permissions", "allow", _]
            | ["enabledMcpjsonServers", _]
            | ["mcpAllowlist", _]
            | [
                "projects",
                _,
                "allowedTools" | "enabledMcpServers" | "enabledMcpjsonServers",
                _
            ]
    )
}

/// What neosian installed: its registrations, its hooks, its own plugin.
fn ours(segments: &[String], value: &Value) -> bool {
    let s: Vec<&str> = segments.iter().map(String::as_str).collect();
    let server = |key: &str| key == OLD || key == NEW;
    match s.as_slice() {
        ["mcpServers" | "mcp" | "servers", key] => server(key),
        ["projects", _, "mcpServers", key] => server(key),
        ["enabledPlugins", "neosian-cache@neosian-local"] => true,
        ["extraKnownMarketplaces", "neosian-local"] => true,
        // The status line neosian's cache maintenance installs.
        ["statusLine"] => value
            .get("command")
            .and_then(Value::as_str)
            .is_some_and(|c| c.contains(" statusline --client ")),
        _ => value
            .get("command")
            .and_then(Value::as_str)
            .is_some_and(hooks::carries),
    }
}

/// Other servers' entries are not mentions: the move does not touch them.
fn other_server(segments: &[String]) -> bool {
    let s: Vec<&str> = segments.iter().map(String::as_str).collect();
    matches!(
        s.as_slice(),
        ["mcpServers" | "mcp", _] | ["projects", _, "mcpServers", _]
    )
}

fn claude_state(segments: &[String]) -> bool {
    let s: Vec<&str> = segments.iter().map(String::as_str).collect();
    matches!(
        s.as_slice(),
        [] | ["projects"]
            | ["projects", _]
            | [
                "projects",
                _,
                "disabledMcpServers"
                    | "enabledMcpServers"
                    | "enabledMcpjsonServers"
                    | "disabledMcpjsonServers"
                    | "allowedTools",
                ..
            ]
    )
}

fn json(path: &Path, text: &str, value: &Value, state: bool, out: &mut Found) {
    let mut stack: Vec<(Vec<String>, &Value)> = vec![(vec![], value)];
    while let Some((segments, value)) = stack.pop() {
        if ours(&segments, value) || other_server(&segments) || (state && !claude_state(&segments))
        {
            continue;
        }
        let hit = |segments: &[String]| {
            let at = jsonedit::locate(text, segments).map_or(0, |(start, _)| start);
            Mention {
                path: path.to_path_buf(),
                line: line_at(text, at),
                allow: allow(segments),
            }
        };
        match value {
            Value::String(s) if names(s) => out.mentions.push(hit(&segments)),
            Value::Object(members) => {
                for (key, member) in members {
                    let mut inner = segments.clone();
                    inner.push(key.clone());
                    let counted =
                        !path_key(&segments) && !ours(&inner, member) && !other_server(&inner);
                    if names(key) && counted {
                        out.mentions.push(Mention {
                            allow: false,
                            ..hit(&inner)
                        });
                    }
                    stack.push((inner, member));
                }
            }
            Value::Array(items) => {
                for (i, item) in items.iter().enumerate() {
                    let mut inner = segments.clone();
                    inner.push(i.to_string());
                    stack.push((inner, item));
                }
            }
            _ => {}
        }
    }
}

fn frontmatter(path: &Path, text: &str, out: &mut Found) {
    let delimiter = |line: &str| line.trim_end_matches(['\r', ' ', '\t']) == "---";
    let mut lines = text.lines().enumerate();
    if !lines
        .next()
        .is_some_and(|(_, l)| delimiter(l.trim_start_matches('\u{feff}')))
    {
        return;
    }
    for (index, line) in lines {
        if delimiter(line) {
            break;
        }
        if names(line) {
            out.mentions.push(Mention {
                path: path.to_path_buf(),
                line: index + 1,
                allow: false,
            });
        }
    }
}

fn toml(path: &Path, text: &str, out: &mut Found) {
    let ours = toml_key::regions(text, &[OLD, NEW]);
    for (index, line) in text.lines().enumerate() {
        let number = index + 1;
        let inside = ours.iter().any(|(a, b)| (*a..=*b).contains(&number));
        // Codex's project and hook-trust headers are keyed by paths.
        let trimmed = line.trim_start();
        let project = trimmed.starts_with("[projects.") || trimmed.starts_with("[hooks.state.");
        if !inside && !project && names(line) {
            out.mentions.push(Mention {
                path: path.to_path_buf(),
                line: number,
                allow: false,
            });
        }
    }
}

fn search(path: &Path, how: How, out: &mut Found) {
    let Some(read) = read::file(path) else {
        return;
    };
    if !names(&read.text) {
        return;
    }
    match how {
        How::Frontmatter => frontmatter(path, &read.text, out),
        How::Toml => match read.text.parse::<toml::Table>() {
            Ok(_) => toml(path, &read.text, out),
            Err(_) => out.unreadable.push(path.to_path_buf()),
        },
        How::Json | How::ClaudeState => match read.json() {
            Some(value) => json(
                path,
                &read.text,
                &value,
                matches!(how, How::ClaudeState),
                out,
            ),
            None => out.unreadable.push(path.to_path_buf()),
        },
    }
}

/// Every mention the search finds for a client.
pub fn find(client: &str, context: &Context) -> Found {
    let mut out = Found::default();
    let mut seen = std::collections::BTreeSet::new();
    for (path, how) in files(client, context) {
        let real = path.canonicalize().unwrap_or_else(|_| path.clone());
        if seen.insert(real) {
            search(&path, how, &mut out);
        }
    }
    out
}
