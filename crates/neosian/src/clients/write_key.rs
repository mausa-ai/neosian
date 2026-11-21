//! The key a write uses (DESIGN §40.53 item 1, from §40.51 item 9): our key
//! where it stands, never `neosian` beside another server's `neosian` or
//! beside our old key where the client reads for this directory, and never
//! the old key rewritten beside our new one.

use super::{
    context::Context,
    files::Failure,
    naming::{NEW, OLD},
    receipt,
    rename::{self, places, text},
    targets::{Document, Target, document, ours_new},
};
use serde_json::Value;

fn failure(key: &str, path: &std::path::Path, fix: &str) -> Failure {
    let path = path.display().to_string();
    Failure::new(text::fill(key, &[("path", &path)]), text::get(fix))
}

/// The containers the client reads for the current directory.
fn here(target: &Target, context: &Context) -> Vec<places::Container> {
    let cwd = context
        .cwd
        .canonicalize()
        .unwrap_or_else(|_| context.cwd.clone());
    places::containers(rename::static_client(&target.client), context)
        .into_iter()
        .filter(|c| {
            c.project
                .as_ref()
                .is_none_or(|p| p.canonicalize().ok().as_ref() == Some(&cwd))
        })
        .collect()
}

pub fn write_key(target: &Target, context: &Context) -> Result<&'static str, Failure> {
    let document = match document(&target.client, &target.path) {
        Document::Absent => Value::default(),
        Document::Read(value) => value,
        Document::Unreadable => {
            return Err(failure("setup_unreadable", &target.path, "fix_unreadable"));
        }
    };
    let old = document.get(target.key).and_then(|s| s.get(OLD)).is_some();
    if let (Err(error), false) = (receipt::load(&target.client, context), old) {
        return Err(Failure::new(error, text::get("fix_receipt")));
    }
    match ours_new(target, &document) {
        Some(true) if old => {
            let shown = target.path.display().to_string();
            let message = text::fill("two", &[("old", &shown), ("new", &shown), ("fix", "")]);
            return Err(Failure::new(
                message.trim_end_matches("; ").to_owned(),
                text::get("two_fix_setup"),
            ));
        }
        _ if old => return Ok(OLD),
        Some(false) => return Err(failure("setup_taken", &target.path, "setup_taken_fix")),
        Some(true) => return Ok(NEW),
        None => {}
    }
    for container in here(target, context) {
        let Ok(entries) = container.entries() else {
            return Err(failure(
                "setup_unreadable",
                &container.file,
                "fix_unreadable",
            ));
        };
        if entries
            .new
            .as_ref()
            .is_some_and(|e| !rename::ours(&container, e, context))
        {
            return Err(failure("setup_taken", &container.file, "setup_taken_fix"));
        }
    }
    let old_nearby = here(target, context).iter().any(|c| {
        c.entries()
            .ok()
            .and_then(|e| e.old)
            .is_some_and(|e| e.get("url").is_none())
    });
    Ok(if old_nearby { OLD } else { NEW })
}

/// Whether the entry under `key` in the target's file, as the client reads it
/// now, is ours: the old key by name, `neosian` by the receipt.
pub fn ours_at(target: &Target, key: &str) -> bool {
    let Some(document) = document(&target.client, &target.path).value() else {
        return false;
    };
    let pointer = super::targets::pointer(target, key);
    match document.pointer(&pointer) {
        Some(entry) if key == NEW => {
            receipt::owns_in(&super::targets::book(target), &target.path, &pointer, entry)
        }
        Some(_) => true,
        None => false,
    }
}
