//! `setup --refresh`: bring the existing user-level registrations to this
//! release, connecting nothing new; `update` runs it on the release it installed
//! (§40.48). A condition found before writing leaves one part alone; a failure
//! while writing restores what this run wrote for that client.

use super::{
    cache_install, codex_skills,
    context::{CLIENTS, Context},
    files::{self, Failure, Record},
    hooks, refresh_hooks,
    refresh_owned::{self, with_executable},
    refresh_parts::{self, Change, Part},
    targets,
};
use crate::{store::fileio, version::VERSION};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    io::{self, Write},
    path::PathBuf,
};

const AFTER_FAILURE: &str = "fix the cause, then run neosian setup --refresh";

fn step(report: Value) -> Result<(), Failure> {
    if report["success"] == true {
        return Ok(());
    }
    let error = report["error"].as_str().unwrap_or("the step failed");
    Err(Failure::new(error, AFTER_FAILURE))
}

/// A planned document is written only over the text it was computed from.
fn unchanged_since_planning(part: &Part) -> Result<(), Failure> {
    if fs::read_to_string(&part.path).ok() == part.read {
        return Ok(());
    }
    let message = format!(
        "{} was changed by its client meanwhile and was not written",
        part.path.display()
    );
    Err(Failure::new(message, AFTER_FAILURE))
}

fn write(part: &Part, context: &Context, created: &mut Vec<PathBuf>) -> Result<(), Failure> {
    match &part.change {
        Some(Change::Json(document, cursor)) => {
            unchanged_since_planning(part)?;
            files::write(&part.path, document, *cursor)
        }
        Some(Change::Text(text)) => {
            unchanged_since_planning(part)?;
            files::write_text(&part.path, text)
        }
        Some(Change::Skills) => step(codex_skills::install(context, "auto", true)),
        Some(Change::Cache(executable)) => {
            let observations = part.path.join("observations");
            if !observations.exists() {
                created.push(observations);
            }
            let context = with_executable(context, executable);
            step(cache_install::install(&context, "user", true))
        }
        None => Ok(()),
    }
}

/// Put back only files this run wrote and that still hold what it wrote, then
/// remove the directories it created if they are empty.
fn restore(record: &Record, created: &[PathBuf]) -> Vec<String> {
    let mut notes = vec![];
    for written in record.written.iter().rev() {
        let path = &written.path;
        if fs::read_to_string(path).ok().as_deref() != Some(&written.text) {
            notes.push(format!(
                "{} was changed by its client meanwhile and is left as it is",
                path.display()
            ));
            continue;
        }
        let restored = match &written.prior {
            Some((text, mode)) => fileio::atomic_write(path, text, false)
                .and_then(|()| fs::set_permissions(path, mode.clone())),
            None => fs::remove_file(path),
        };
        if restored.is_err() {
            notes.push(format!("{} could not be restored", path.display()));
        }
    }
    let directories: BTreeSet<_> = record.created.iter().chain(created).collect();
    let mut directories: Vec<_> = directories.into_iter().collect();
    directories.sort_by_key(|directory| std::cmp::Reverse(directory.components().count()));
    for directory in directories {
        // Only an empty directory can be removed; anything else stays.
        let _ = fs::remove_dir(directory);
    }
    notes
}

fn apply(parts: &[Part], context: &Context) -> Result<(), Failure> {
    let mut created = vec![];
    let (result, record) = files::recorded(|| {
        parts
            .iter()
            .try_for_each(|part| write(part, context, &mut created))
    });
    let Err(mut failure) = result else {
        return Ok(());
    };
    let notes = restore(&record, &created);
    if !notes.is_empty() {
        failure.message = format!("{}; {}", failure.message, notes.join("; "));
    }
    failure.hint = AFTER_FAILURE.into();
    Err(failure)
}

fn plan(client: &str, context: &Context) -> Vec<Part> {
    let mut mcp = refresh_parts::mcp(client, context);
    let hooks = refresh_hooks::hooks(client, context, mcp.as_ref());
    // When both parts edit one file, the hooks document already carries the MCP change.
    if let (Some(first), Some(second)) = (mcp.as_mut(), hooks.as_ref())
        && first.path == second.path
        && matches!(second.change, Some(Change::Json(..)))
    {
        first.change = None;
    }
    let mut parts: Vec<Part> = mcp.into_iter().chain(hooks).collect();
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
    let failure = apply(&parts, context).err();
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
        "parts": parts.iter().map(Part::report).collect::<Vec<_>>()}))
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
    let restart: Vec<_> = clients
        .iter()
        .filter(|c| c["result"] == "updated")
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::Scratch;
    use std::collections::BTreeMap;

    #[test]
    fn a_file_its_client_changed_after_planning_is_never_written() {
        let scratch = Scratch::new("refresh-planned-text");
        scratch.plant("mcp.json", "before\n");
        scratch.plant("hooks.json", "planned from\n");
        let at = |name: &str| scratch.path().join(name);
        let parts = [
            Part::new("mcp", at("mcp.json"))
                .update(Change::Text("ours\n".into()), Some("before\n".into())),
            Part::new("hooks", at("hooks.json"))
                .update(Change::Text("ours\n".into()), Some("planned from\n".into())),
        ];
        // The client writes the second file after the plan read it.
        scratch.plant("hooks.json", "the client's own\n");
        let context = Context {
            home: scratch.path().into(),
            cwd: scratch.path().into(),
            executable: "neosian".into(),
            env: BTreeMap::new(),
            directories: BTreeMap::new(),
        };
        let failure = apply(&parts, &context).unwrap_err();
        assert!(
            failure
                .message
                .ends_with("hooks.json was changed by its client meanwhile and was not written"),
            "{}",
            failure.message
        );
        assert_eq!(failure.hint, AFTER_FAILURE);
        assert_eq!(
            fs::read_to_string(at("hooks.json")).unwrap(),
            "the client's own\n"
        );
        // The first file was written by this run and still held its bytes: restored.
        assert_eq!(fs::read_to_string(at("mcp.json")).unwrap(), "before\n");
    }
}
