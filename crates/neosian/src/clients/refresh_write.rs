//! Writing a client's refresh: the steps that run its own CLI first, then the
//! documents planned again from the bytes on disk; a failure puts back only
//! what this run wrote and still holds its bytes (§40.48, #426).

use super::{
    cache_install, codex_skills,
    context::Context,
    files::{self, Failure, Record},
    refresh::documents,
    refresh_owned::with_executable,
    refresh_parts::{Change, Part},
};
use crate::store::fileio;
use serde_json::Value;
use std::{collections::BTreeSet, fs, path::PathBuf};

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

fn is_step(part: &Part) -> bool {
    matches!(part.change, Some(Change::Skills | Change::Cache(_)))
}

/// The steps go first: the plugin step runs the client's own CLI, which writes
/// the client's settings itself. The documents are then planned again from the
/// bytes on disk, so a CLI failure leaves no document of ours written.
pub(super) fn apply(
    client: &str,
    parts: Vec<Part>,
    context: &Context,
) -> (Vec<Part>, Option<Failure>) {
    let mut created = vec![];
    let ((parts, result), record) = files::recorded(|| {
        let steps = parts.iter().filter(|p| is_step(p));
        if let Err(failure) = steps
            .clone()
            .try_for_each(|p| write(p, context, &mut created))
        {
            return (parts, Err(failure));
        }
        let parts = if steps.count() > 0 {
            let owned = parts
                .into_iter()
                .filter(|p| matches!(p.kind, "skills" | "cache"));
            documents(client, context)
                .into_iter()
                .chain(owned)
                .collect()
        } else {
            parts
        };
        let documents = parts.iter().filter(|p| !is_step(p));
        let result = documents
            .clone()
            .try_for_each(|p| write(p, context, &mut created));
        (parts, result)
    });
    let Err(mut failure) = result else {
        return (parts, None);
    };
    let notes = restore(&record, &created);
    if !notes.is_empty() {
        failure.message = format!("{}; {}", failure.message, notes.join("; "));
    }
    failure.hint = AFTER_FAILURE.into();
    (parts, Some(failure))
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
        let failure = apply("cursor", parts.into(), &context).1.unwrap();
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
