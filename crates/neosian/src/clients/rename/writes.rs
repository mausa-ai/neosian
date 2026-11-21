//! A registration file edited in place for a move (DESIGN §40.53 item 1):
//! the servers member's key renamed, or the old one removed beside an equal
//! `neosian`, every other byte kept and the result checked against the
//! intended document; Codex's table headers rewritten.

use super::{claude_cli::AFTER_FAILURE, jsonedit, places::Container, read, text, toml_key};
use crate::clients::files::{self, Failure};
use serde_json::Value;

/// A JSON file's servers member with `from` renamed to `to`, or `from`
/// removed when `remove` (the file's bytes kept outside the edit).
pub(super) fn json_file(
    container: &Container,
    member: &str,
    from: &str,
    to: &str,
    remove: bool,
) -> Result<(), Failure> {
    let path = &container.file;
    let shown = path.display().to_string();
    let read = read::file(path)
        .ok_or_else(|| Failure::new(text::fill("changed", &[("path", &shown)]), AFTER_FAILURE))?;
    let changed = || Failure::new(text::fill("changed", &[("path", &shown)]), AFTER_FAILURE);
    if let Some(key) = read.blocked() {
        return Err(Failure::new(
            text::fill(key, &[("path", &shown)]),
            AFTER_FAILURE,
        ));
    }
    let (Some(mut intended), Some(_)) = (read.json(), read.original()) else {
        return Err(changed());
    };
    let map = intended
        .get_mut(member)
        .and_then(Value::as_object_mut)
        .ok_or_else(changed)?;
    let segments = vec![member.to_owned()];
    let edited = if remove {
        map.remove(from).ok_or_else(changed)?;
        jsonedit::remove_member(&read.text, &segments, from, &intended)
    } else {
        let entry = map.remove(from).ok_or_else(changed)?;
        if map.contains_key(to) {
            return Err(changed());
        }
        map.insert(to.into(), entry);
        jsonedit::rename_key(&read.text, &segments, from, to, &intended)
    };
    let edited = edited.ok_or_else(changed)?;
    files::write_text(path, &read.with_bom(&edited))
}

pub(super) fn toml_file(container: &Container, from: &str, to: &str) -> Result<(), Failure> {
    let path = &container.file;
    let current = std::fs::read_to_string(path)?;
    let edited = toml_key::moved(&current, from, to).map_err(|reason| {
        let shown = path.display().to_string();
        Failure::new(
            text::fill("toml_refused", &[("path", &shown), ("reason", &reason)]),
            AFTER_FAILURE,
        )
    })?;
    files::write_text(path, &edited)
}

/// A local scope the CLI cannot address, moved in `.claude.json` in place.
pub(super) fn in_project(
    container: &Container,
    project: &str,
    from: &str,
    to: &str,
    remove: bool,
) -> Result<(), Failure> {
    let path = &container.file;
    let shown = path.display().to_string();
    let changed = || Failure::new(text::fill("changed", &[("path", &shown)]), AFTER_FAILURE);
    let read = read::file(path).ok_or_else(changed)?;
    let mut intended = read.json().ok_or_else(changed)?;
    let at = jsonedit::join(&["projects", project, "mcpServers"]);
    let map = intended
        .pointer_mut(&at)
        .and_then(Value::as_object_mut)
        .ok_or_else(changed)?;
    let segments = jsonedit::segments(&at);
    let edited = if remove {
        map.remove(from).ok_or_else(changed)?;
        jsonedit::remove_member(&read.text, &segments, from, &intended)
    } else {
        let entry = map.remove(from).ok_or_else(changed)?;
        map.insert(to.into(), entry);
        jsonedit::rename_key(&read.text, &segments, from, to, &intended)
    };
    files::write_text(path, &read.with_bom(&edited.ok_or_else(changed)?))
}
