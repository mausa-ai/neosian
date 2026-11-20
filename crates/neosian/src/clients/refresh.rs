//! `setup --refresh`: bring the existing user-level registrations to this
//! release, connecting nothing new; `update` runs it on the release it installed
//! (§40.48). Each client is all or nothing.

use super::{
    cache_install, codex_skills,
    context::{CLIENTS, Context},
    files::{self, Failure},
    hooks,
    refresh_parts::{self, Change, Part},
    targets,
};
use crate::{store::fileio, version::VERSION};
use serde_json::{Value, json};
use std::{
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
};

/// Prior bytes of every file a client's refresh may write, restored on failure.
#[derive(Default)]
struct Journal(Vec<(PathBuf, Option<(String, fs::Permissions)>)>);

impl Journal {
    fn keep(&mut self, path: &Path) -> Result<(), Failure> {
        if self.0.iter().any(|(kept, _)| kept == path) {
            return Ok(());
        }
        let prior = match fs::read_to_string(path) {
            Ok(text) => Some((text, fs::metadata(path)?.permissions())),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => {
                let message = format!("cannot read {}: {error}", path.display());
                return Err(Failure::new(message, files::FIX));
            }
        };
        self.0.push((path.into(), prior));
        Ok(())
    }

    fn restore(&self) -> Vec<String> {
        let mut failed = vec![];
        for (path, prior) in self.0.iter().rev() {
            let restored = match prior {
                Some((text, mode)) => fileio::atomic_write(path, text, false)
                    .and_then(|()| fs::set_permissions(path, mode.clone())),
                None => fs::remove_file(path).or_else(|error| match error.kind() {
                    io::ErrorKind::NotFound => Ok(()),
                    _ => Err(error),
                }),
            };
            if restored.is_err() {
                failed.push(path.display().to_string());
            }
        }
        failed
    }
}

fn with_executable(context: &Context, executable: &str) -> Context {
    Context {
        home: context.home.clone(),
        cwd: context.cwd.clone(),
        executable: executable.into(),
        env: context.env.clone(),
        directories: context.directories.clone(),
    }
}

fn step(report: Value) -> Result<(), Failure> {
    if report["success"] == true {
        return Ok(());
    }
    Err(Failure::new(
        report["error"].as_str().unwrap_or("the step failed"),
        report["hint"].as_str().unwrap_or(files::FIX),
    ))
}

fn write(part: &Part, context: &Context) -> Result<(), Failure> {
    match &part.change {
        Some(Change::Json(document, cursor)) => files::write(&part.path, document, *cursor),
        Some(Change::Text(text)) => files::write_text(&part.path, text),
        Some(Change::Skills) => step(codex_skills::install(context, "auto", true)),
        Some(Change::Cache(executable)) => step(cache_install::install(
            &with_executable(context, executable),
            "user",
            true,
        )),
        None => Ok(()),
    }
}

/// Keep every file first, then write; a failed step restores what was kept.
fn apply(parts: &[Part], context: &Context) -> Result<(), Failure> {
    let mut journal = Journal::default();
    for part in parts {
        let paths = match &part.change {
            Some(Change::Json(..) | Change::Text(_)) => vec![part.path.clone()],
            Some(Change::Skills) => codex_skills::owned(context),
            Some(Change::Cache(_)) => cache_install::owned(context, "user"),
            None => vec![],
        };
        for path in paths {
            journal.keep(&path)?;
        }
    }
    let Err(mut failure) = parts.iter().try_for_each(|part| write(part, context)) else {
        return Ok(());
    };
    let unrestored = journal.restore();
    if !unrestored.is_empty() {
        failure.message = format!(
            "{}; these files could not be restored: {}",
            failure.message,
            unrestored.join(", ")
        );
    }
    Err(failure)
}

/// Codex's managed skill, only where its receipt exists; `auto` keeps the saved choice.
fn skills(context: &Context) -> Result<Option<Part>, Failure> {
    let owned = codex_skills::owned(context);
    if !owned[0].is_file() {
        return Ok(None);
    }
    let report = codex_skills::install(context, "auto", false);
    if report["success"] != true {
        return Err(Failure::new(
            report["error"]
                .as_str()
                .unwrap_or("the managed skill cannot be read"),
            report["hint"].as_str().unwrap_or(files::FIX),
        ));
    }
    let part = Part::new("skills", owned[1].clone());
    Ok(Some(match report["action"].as_str() {
        Some("unchanged") => part,
        Some("keep_disabled") => Part {
            action: "off",
            ..part
        },
        _ => part.update(Change::Skills),
    }))
}

/// Claude Code's bundled plugin at the user level; an explicit disable is kept.
fn cache(context: &Context, executable: Option<String>) -> Option<Part> {
    let status = cache_install::status(context);
    let level = &status["levels"][0];
    let part = Part::new("cache", level["directory"].as_str().unwrap_or("").into());
    if level["enabled"] == false {
        return Some(Part {
            action: "off",
            ..part
        });
    }
    if level["installed"] != true {
        return None;
    }
    let Some(executable) = executable else {
        return Some(part.left_alone("the client's registrations run another installation"));
    };
    if level["observation_configured"] != true {
        return Some(part.left_alone(
            "the statusline no longer carries the observation bridge; setup --client claude-code --write restores it",
        ));
    }
    Some(if level["current"] == true {
        part
    } else {
        part.update(Change::Cache(executable))
    })
}

/// Registrations in the current project, listed and never edited.
fn shared(client: &str, context: &Context) -> Vec<(&'static str, PathBuf)> {
    let mut found = vec![];
    let (user, shared) = (
        targets::mcp(client, context, "user"),
        targets::mcp(client, context, "project"),
    );
    if shared.path != user.path && targets::registered(&shared).is_some() {
        found.push(("mcp", shared.path));
    }
    let (user, shared) = (
        targets::hooks(client, context, "user"),
        targets::hooks(client, context, "project"),
    );
    if shared.path != user.path && hooks::installed(&shared).is_some() {
        found.push(("hooks", shared.path));
    }
    if client == "claude-code" {
        let level = &cache_install::status(context)["levels"][1];
        if level["installed"] == true {
            found.push(("cache", level["directory"].as_str().unwrap_or("").into()));
        }
    }
    found
}

fn plan(client: &str, context: &Context) -> Result<Vec<Part>, Failure> {
    let mut parts: Vec<Part> = [
        refresh_parts::mcp(client, context)?,
        refresh_parts::hooks(client, context)?,
    ]
    .into_iter()
    .flatten()
    .collect();
    if parts.is_empty() {
        return Ok(parts);
    }
    if client == "codex" {
        parts.extend(skills(context)?);
    }
    if client == "claude-code" {
        let executable = parts.iter().rev().find_map(|p| p.executable.clone());
        parts.extend(cache(context, executable));
    }
    Ok(parts)
}

/// One client's report and whether it runs this installation; `None` when unregistered.
fn refresh(client: &str, context: &Context) -> Option<(Value, bool)> {
    let label = targets::mcp(client, context, "user").label;
    let registered = targets::registered(&targets::mcp(client, context, "user")).is_some()
        || targets::active(client, context, "user")
            .iter()
            .any(|t| hooks::installed(t).is_some());
    if !registered {
        return None;
    }
    let (parts, failure) = match plan(client, context) {
        Ok(parts) => {
            let failure = apply(&parts, context).err();
            (parts, failure)
        }
        Err(failure) => (vec![], Some(failure)),
    };
    let result = if failure.is_some() {
        "failed"
    } else if parts.iter().any(|p| p.change.is_some()) {
        "updated"
    } else if parts.iter().any(|p| p.action == "left_alone") {
        "left_alone"
    } else {
        "current"
    };
    let runs_here = failure.is_some()
        || parts
            .iter()
            .any(|p| matches!(p.kind, "mcp" | "hooks") && p.executable.is_some());
    let report = json!({"client": client, "label": label, "result": result,
        "error": failure.as_ref().map(|f| &f.message), "hint": failure.as_ref().map(|f| &f.hint),
        "parts": parts.iter().map(Part::report).collect::<Vec<_>>()});
    Some((report, runs_here))
}

pub fn run(context: &Context, json: bool, out: &mut impl Write) -> io::Result<u8> {
    let (mut clients, mut project, mut restart) = (vec![], vec![], vec![]);
    for client in CLIENTS {
        let label = targets::mcp(client, context, "user").label;
        for (kind, path) in shared(client, context) {
            project.push(json!({"client": client, "label": label, "kind": kind, "path": path}));
        }
        if let Some((report, runs_here)) = refresh(client, context) {
            if runs_here {
                restart.push(label);
            }
            clients.push(report);
        }
    }
    let success = clients.iter().all(|c| c["result"] != "failed");
    let report = json!({"success": success, "version": VERSION, "level": "user",
        "clients": clients, "project": project, "restart": restart});
    if json {
        writeln!(out, "{report}")?;
    } else {
        super::refresh_output::render(&report, out)?;
    }
    Ok(u8::from(!success))
}
