//! `setup --refresh`: bring the existing user-level registrations to this
//! release, connecting nothing new; `update` runs it on the release it installed
//! (§40.48). A condition found before writing leaves one part alone; a failure
//! while writing restores what this run wrote for that client.

use super::{
    cache_install, codex_skills,
    context::{CLIENTS, Context},
    hooks, refresh_hooks, refresh_owned,
    refresh_parts::{self, Change, Part},
    refresh_write, targets,
};
use crate::version::VERSION;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    io::{self, Write},
    path::PathBuf,
};

/// The MCP and hooks parts. When both edit one file (Muse), the hooks document
/// already carries the MCP change and is the one write.
pub(super) fn documents(client: &str, context: &Context) -> Vec<Part> {
    let mut mcp = refresh_parts::mcp(client, context);
    let hooks = refresh_hooks::hooks(client, context, mcp.as_ref());
    if let (Some(first), Some(second)) = (mcp.as_mut(), hooks.as_ref())
        && first.path == second.path
        && matches!(second.change, Some(Change::Json(..)))
    {
        first.change = None;
    }
    mcp.into_iter().chain(hooks).collect()
}

fn plan(client: &str, context: &Context) -> Vec<Part> {
    let mut parts = documents(client, context);
    if parts.is_empty() {
        return parts;
    }
    let executable = parts.iter().rev().find_map(|p| p.executable.clone());
    if client == "codex" {
        parts.extend(refresh_owned::skills(context, executable.is_some()));
    }
    if client == "claude-code" {
        parts.extend(refresh_owned::cache(context, executable));
    }
    parts
}

/// Every file this client's refresh may change, its own CLI included.
fn touched(parts: &[Part], context: &Context) -> BTreeSet<PathBuf> {
    let mut paths: BTreeSet<_> = parts.iter().map(|p| p.path.clone()).collect();
    for part in parts {
        match part.change {
            Some(Change::Skills) => paths.extend(codex_skills::owned(context)),
            Some(Change::Cache(_)) => paths.extend(cache_install::owned(context, "user")),
            _ => {}
        }
    }
    paths
}

/// One client's report; `None` when it has no user-level registration.
fn refresh(client: &str, context: &Context) -> Option<Value> {
    let label = targets::mcp(client, context, "user").label;
    let registered = targets::registered(&targets::mcp(client, context, "user")).is_some()
        || targets::active(client, context, "user")
            .iter()
            .any(|t| hooks::installed(t).is_some());
    if !registered {
        return None;
    }
    let parts = plan(client, context);
    let paths = touched(&parts, context);
    let before: Vec<_> = paths.iter().map(|path| fs::read(path).ok()).collect();
    let (mut parts, failure) = refresh_write::apply(client, parts, context);
    // Whatever this run left different, whoever wrote it, is reported.
    let changed: Vec<_> = paths
        .iter()
        .zip(&before)
        .filter(|(path, prior)| fs::read(path).ok() != **prior)
        .map(|(path, _)| path)
        .collect();
    if failure.is_some() {
        let now = plan(client, context);
        for part in parts.iter_mut().filter(|p| p.action == "update") {
            part.kept = Some(
                now.iter()
                    .any(|n| n.kind == part.kind && n.action == "unchanged"),
            );
        }
    }
    let result = if failure.is_some() {
        "failed"
    } else if parts.iter().any(|p| p.action == "update") {
        "updated"
    } else if parts.iter().any(|p| p.action == "left_alone") {
        "left_alone"
    } else {
        "current"
    };
    Some(json!({"client": client, "label": label, "result": result,
        "error": failure.as_ref().map(|f| &f.message), "hint": failure.as_ref().map(|f| &f.hint),
        "changed": changed, "parts": parts.iter().map(Part::report).collect::<Vec<_>>()}))
}

pub fn run(context: &Context, json: bool, out: &mut impl Write) -> io::Result<u8> {
    let (mut clients, mut project) = (vec![], vec![]);
    for client in CLIENTS {
        let label = targets::mcp(client, context, "user").label;
        for (kind, path) in refresh_owned::shared(client, context) {
            project.push(json!({"client": client, "label": label, "kind": kind, "path": path}));
        }
        clients.extend(refresh(client, context));
    }
    let success = clients.iter().all(|c| c["result"] != "failed");
    // A client is restarted when any of its files changed, including one a
    // failed client kept.
    let restart: Vec<_> = clients
        .iter()
        .filter(|c| {
            c["changed"]
                .as_array()
                .is_some_and(|changed| !changed.is_empty())
        })
        .map(|c| c["label"].clone())
        .collect();
    let report = json!({"success": success, "version": VERSION, "level": "user",
        "clients": clients, "project": project, "restart": restart});
    if json {
        writeln!(out, "{report}")?;
    } else {
        super::refresh_output::render(&report, out)?;
    }
    Ok(u8::from(!success))
}
