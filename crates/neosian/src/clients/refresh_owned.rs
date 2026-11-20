//! What a release owns outright beside a client's registrations, Codex's skill
//! and Claude Code's plugin, refreshed only while the client runs this
//! installation; and the project registrations, which are only listed (§40.48).

use super::{
    cache_install, codex_skills,
    context::Context,
    hooks,
    refresh_parts::{Change, Part, RETRY},
    runner, targets,
};
use std::path::PathBuf;

const ANOTHER: &str = "the client's registrations run another installation";

pub fn with_executable(context: &Context, executable: &str) -> Context {
    Context {
        home: context.home.clone(),
        cwd: context.cwd.clone(),
        executable: executable.into(),
        env: context.env.clone(),
        directories: context.directories.clone(),
    }
}

/// Codex's managed skill, only where its receipt exists; `auto` keeps the saved choice.
pub fn skills(context: &Context, runs_here: bool) -> Option<Part> {
    let owned = codex_skills::owned(context);
    if !owned[0].is_file() {
        return None;
    }
    let part = Part::new("skills", owned[1].clone());
    let report = codex_skills::install(context, "auto", false);
    if report["success"] != true {
        let error = report["error"]
            .as_str()
            .unwrap_or("the skill cannot be read");
        return Some(part.left_alone(format!("{error}; move your changes aside, {RETRY}")));
    }
    Some(match report["action"].as_str() {
        Some("unchanged") => part,
        Some("keep_disabled") => part.off(),
        Some("remove") => {
            let directory = owned[1].parent().unwrap_or(&owned[1]).display().to_string();
            part.left_alone(format!(
                "an earlier opt-out did not finish removing the skill; delete {directory} to finish it"
            ))
        }
        _ if !runs_here => part.left_alone(ANOTHER),
        _ => part.update(Change::Skills, None).guarded(&owned),
    })
}

/// Claude Code's bundled plugin, only while Claude's settings list it enabled.
pub fn cache(context: &Context, executable: Option<String>) -> Option<Part> {
    let status = cache_install::status(context);
    let level = &status["levels"][0];
    let part = Part::new("cache", level["directory"].as_str().unwrap_or("").into());
    if level["enabled"] == false {
        return Some(part.off());
    }
    if level["enabled"] != true || level["installed"] != true {
        return None;
    }
    let Some(executable) = executable else {
        return Some(part.left_alone(ANOTHER));
    };
    if level["observation_configured"] != true {
        return Some(part.left_alone(
            "the statusline no longer carries the observation bridge; run setup for Claude Code again to restore it, or setup --client claude-code --claude-cache off --write to stop the plugin",
        ));
    }
    if level["current"] == true {
        return Some(part);
    }
    if runner::which("claude", &context.env).is_none() {
        return Some(part.left_alone(format!(
            "the claude CLI is not on PATH, and the plugin is installed through it; install it, {RETRY}"
        )));
    }
    let preview = cache_install::install(&with_executable(context, &executable), "user", false);
    if preview["success"] != true {
        let error = preview["error"]
            .as_str()
            .unwrap_or("the plugin cannot be prepared");
        return Some(part.left_alone(format!("{error}; {RETRY}")));
    }
    Some(
        part.update(Change::Cache(executable), None)
            .guarded(&cache_install::owned(context, "user")),
    )
}

/// Registrations in the current project, listed and never edited.
pub fn shared(client: &str, context: &Context) -> Vec<(&'static str, PathBuf)> {
    let mut found = vec![];
    let (user, project) = (
        targets::mcp(client, context, "user"),
        targets::mcp(client, context, "project"),
    );
    if project.path != user.path && targets::registered(&project).is_some() {
        found.push(("mcp", project.path));
    }
    let (user, project) = (
        targets::hooks(client, context, "user"),
        targets::hooks(client, context, "project"),
    );
    if project.path != user.path && hooks::installed(&project).is_some() {
        found.push(("hooks", project.path));
    }
    if client == "claude-code" {
        let level = &cache_install::status(context)["levels"][1];
        if level["installed"] == true {
            found.push(("cache", level["directory"].as_str().unwrap_or("").into()));
        }
    }
    found
}
