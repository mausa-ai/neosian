//! The files the mention search reads for each client (DESIGN §40.53 item
//! 3): its own configuration and the frontmatter of its agents, skills and
//! commands, at user level and in each visited project.

use super::{places, search::How};
use crate::clients::context::Context;
use std::path::{Path, PathBuf};

/// Markdown files below `dir` (64 levels), sorted.
fn markdown(dir: &Path) -> Vec<PathBuf> {
    walk(dir, "md")
}

fn skills(dir: &Path, except: &[&str]) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return vec![];
    };
    let mut found: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .filter(|e| !except.contains(&e.file_name().to_string_lossy().as_ref()))
        .map(|e| e.path().join("SKILL.md"))
        .collect();
    found.sort();
    found
}

/// The files the search reads for a client, with how each is read.
pub(super) fn files(client: &str, context: &Context) -> Vec<(PathBuf, How)> {
    let base = context.directory(client);
    let mut list: Vec<(PathBuf, How)> = vec![];
    let md = |list: &mut Vec<(PathBuf, How)>, paths: Vec<PathBuf>| {
        list.extend(paths.into_iter().map(|p| (p, How::Frontmatter)));
    };
    let projects = places::projects(client, context);
    match client {
        "claude-code" => {
            for name in ["settings.json", "settings.local.json"] {
                list.push((base.join(name), How::Json));
            }
            list.push((super::claude_cli::file(context), How::ClaudeState));
            md(&mut list, markdown(&base.join("agents")));
            md(&mut list, skills(&base.join("skills"), &[]));
            md(&mut list, markdown(&base.join("commands")));
            for p in &projects {
                let dot = p.join(".claude");
                for name in ["settings.json", "settings.local.json"] {
                    list.push((dot.join(name), How::Json));
                }
                md(&mut list, markdown(&dot.join("agents")));
                md(&mut list, skills(&dot.join("skills"), &[]));
                md(&mut list, markdown(&dot.join("commands")));
            }
        }
        "codex" => {
            for dir in
                std::iter::once(base.clone()).chain(projects.iter().map(|p| p.join(".codex")))
            {
                list.push((dir.join("config.toml"), How::Toml));
                list.push((dir.join("hooks.json"), How::Json));
                let agents = walk(&dir.join("agents"), "toml");
                list.extend(agents.into_iter().map(|p| (p, How::Toml)));
                md(&mut list, skills(&dir.join("skills"), &["neosian-upkeep"]));
            }
            let profiles = std::fs::read_dir(&base)
                .into_iter()
                .flatten()
                .filter_map(Result::ok);
            for profile in profiles.map(|e| e.path()) {
                if profile.to_string_lossy().ends_with(".config.toml") {
                    list.push((profile, How::Toml));
                }
            }
        }
        "opencode" => {
            for dir in [base.clone(), context.cwd.clone()] {
                for name in ["opencode.json", "opencode.jsonc"] {
                    list.push((dir.join(name), How::Json));
                }
            }
            for dir in [base.clone(), context.cwd.join(".opencode")] {
                for name in ["agent", "agents", "mode", "modes"] {
                    md(&mut list, markdown(&dir.join(name)));
                }
            }
        }
        "cursor" => {
            for (dir, cli) in [
                (base.clone(), "cli-config.json"),
                (context.cwd.join(".cursor"), "cli.json"),
            ] {
                for name in ["mcp.json", cli, "permissions.json", "hooks.json"] {
                    list.push((dir.join(name), How::Json));
                }
            }
        }
        "muse-code" => {
            list.push((base.join("settings.json"), How::Json));
            md(&mut list, markdown(&base.join("agents")));
            for p in &projects {
                list.push((p.join(".muse/hooks.json"), How::Json));
                md(&mut list, markdown(&p.join(".agents/agents")));
            }
        }
        _ => {}
    }
    for container in places::containers(super::static_client(client), context) {
        if !matches!(container.kind, places::Kind::Claude(_))
            && !list.iter().any(|(p, _)| *p == container.file)
        {
            let how = if matches!(container.kind, places::Kind::Toml) {
                How::Toml
            } else {
                How::Json
            };
            list.push((container.file, how));
        }
    }
    list
}

/// Files with `extension` below `dir` (64 levels, links to folders not
/// followed), sorted.
fn walk(dir: &Path, extension: &str) -> Vec<PathBuf> {
    let mut found = vec![];
    let mut stack = vec![(dir.to_path_buf(), 0)];
    while let Some((at, depth)) = stack.pop() {
        let entries = std::fs::read_dir(&at)
            .into_iter()
            .flatten()
            .filter_map(Result::ok);
        for path in entries.map(|e| e.path()) {
            match std::fs::symlink_metadata(&path) {
                Ok(meta) if meta.is_dir() && depth < 64 => stack.push((path, depth + 1)),
                Ok(_) if path.extension().is_some_and(|e| e == extension) => found.push(path),
                _ => {}
            }
        }
    }
    found.sort();
    found
}
