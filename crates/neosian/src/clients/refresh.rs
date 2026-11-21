//! `setup --refresh`: bring the existing user-level registrations to this
//! release, connecting nothing new; `update` runs it on the release it installed
//! (§40.48). A condition found before writing leaves one part alone; a failure
//! while writing restores what this run wrote for that client.

use super::{
    cache_install, codex_skills,
    context::{CLIENTS, Context},
    hooks, refresh_hooks, refresh_owned,
    refresh_parts::{self, Change, Part},
    refresh_write, rename, targets,
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

/// A client's report with the rename's members; a client with no
/// registration left after the move still reports what the rename did, and
/// two of our servers make it fail (§40.53 item 2).
fn with_rename(
    report: Option<Value>,
    outcome: &rename::moves::Outcome,
    context: &Context,
) -> Option<Value> {
    let client = outcome.plan.client;
    let state = outcome.state;
    let mut report = match report {
        Some(report) => report,
        None if state == rename::moves::State::Unchanged && outcome.plan.hosted.is_empty() => {
            return None;
        }
        None => json!({"client": client, "label": outcome.plan.label, "result": "current",
            "error": null, "hint": null, "changed": [], "parts": []}),
    };
    if state == rename::moves::State::Moved {
        let files: std::collections::BTreeSet<PathBuf> = outcome
            .plan
            .steps
            .iter()
            .map(|s| s.container.file.clone())
            .collect();
        let changed = report["changed"].as_array_mut().expect("changed");
        for file in files {
            if !changed.contains(&json!(file)) {
                changed.push(json!(file));
            }
        }
        if report["result"] == "current" || report["result"] == "left_alone" {
            report["result"] = json!("updated");
        }
    }
    if state == rename::moves::State::Left && report["result"] == "current" {
        report["result"] = json!("left_alone");
    }
    let (pairs, two) = rename::report::two(client, context, false);
    let failure = outcome.error.clone().or_else(|| two.first().cloned());
    if let Some(error) = failure {
        report["result"] = json!("failed");
        report["error"] = json!(error);
        report["hint"] = json!(rename::claude_cli_hint());
    }
    report["rename"] = rename::report::member(outcome);
    report["rename"]["two_servers"] = json!(pairs);
    report["rename_lines"] = json!(rename::report::lines(outcome));
    Some(report)
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

pub fn run(context: &Context, forced: bool, json: bool, out: &mut impl Write) -> io::Result<u8> {
    // The rename goes first (§40.53): each client moved whole or left as it is.
    let outcomes = rename::run(context, forced, None);
    let (mut clients, mut project) = (vec![], vec![]);
    for outcome in &outcomes {
        let client = outcome.plan.client;
        let label = targets::mcp(client, context, "user").label;
        if CLIENTS.contains(&client) {
            for (kind, path) in refresh_owned::shared(client, context) {
                project.push(json!({"client": client, "label": label, "kind": kind, "path": path}));
            }
        }
        let report = CLIENTS
            .contains(&client)
            .then(|| refresh(client, context))
            .flatten();
        if let Some(report) = with_rename(report, outcome, context) {
            clients.push(report);
        }
    }
    // A project file the rename moved is reported with its client, not here.
    let moved: BTreeSet<PathBuf> = outcomes
        .iter()
        .filter(|o| o.state == rename::moves::State::Moved)
        .flat_map(|o| o.plan.steps.iter().map(|s| s.container.file.clone()))
        .collect();
    project.retain(|entry| !moved.contains(&PathBuf::from(entry["path"].as_str().unwrap_or(""))));
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
