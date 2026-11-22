//! A registration file edited in place for a move (DESIGN §40.53 item 1):
//! the servers member's key renamed, or the old one removed beside an equal
//! `neosian`, every other byte kept and the result checked against the
//! intended document; Codex's table headers rewritten.

use super::{claude_cli::AFTER_FAILURE, jsonedit, places::Container, read, text, toml_key};
use crate::clients::files::{self, Failure};
use serde_json::Value;

/// The edited text of a JSON file's servers member, `from` renamed to `to`
/// or removed when `remove`, checked against the intended document.
fn json_edit(
    container: &Container,
    member: &str,
    from: &str,
    to: &str,
    remove: bool,
) -> Result<(read::Read, String), Failure> {
    let path = &container.file;
    let shown = path.display().to_string();
    let changed = || Failure::new(text::fill("changed", &[("path", &shown)]), AFTER_FAILURE);
    let read = read::file(path).ok_or_else(changed)?;
    if let Some(key) = read.blocked() {
        return Err(Failure::new(
            text::fill(key, &[("path", &shown)]),
            AFTER_FAILURE,
        ));
    }
    // A `.jsonc` with comments is edited on its blanked text (comments and
    // trailing commas as spaces, every offset kept) and the change spliced
    // into the original; the result is read again: the same keys apart from
    // ours, the same values, every comment byte untouched.
    let commented = read.json().is_none() && path.extension().is_some_and(|e| e == "jsonc");
    let source = if commented {
        read::blank(&read.text)
    } else {
        read.text.clone()
    };
    let (Some(mut intended), Some(_)) = (read.jsonc(), read.original()) else {
        return Err(changed());
    };
    let not_editable = || {
        Failure::new(
            text::fill("not_editable", &[("path", &shown)]),
            AFTER_FAILURE,
        )
    };
    let map = intended
        .get_mut(member)
        .and_then(Value::as_object_mut)
        .ok_or_else(changed)?;
    let segments = vec![member.to_owned()];
    let edited = if remove {
        map.remove(from).ok_or_else(changed)?;
        if commented {
            jsonedit::member_span(&source, &segments, from)
                .and_then(|(start, end)| removed_keeping_comments(&read.text, start, end))
        } else {
            jsonedit::remove_member(&source, &segments, from, &intended)
        }
    } else {
        let entry = map.remove(from).ok_or_else(changed)?;
        if map.contains_key(to) {
            return Err(changed());
        }
        map.insert(to.into(), entry);
        jsonedit::rename_key(&source, &segments, from, to, &intended).and_then(|edited| {
            if commented {
                splice(&read.text, &source, &edited)
            } else {
                Some(edited)
            }
        })
    };
    let edited = edited.ok_or_else(not_editable)?;
    if commented {
        let again: Option<Value> = serde_json::from_str(&read::blank(&edited)).ok();
        if again.as_ref() != Some(&intended)
            || read::comments(&edited) != read::comments(&read.text)
        {
            return Err(not_editable());
        }
    }
    Ok((read, edited))
}

/// The member removed from a commented file over `start..end` (offsets
/// shared by the original and its blanked text), keeping each comment the
/// span holds with the spacing before it and a line comment's line end,
/// then (when it kept one) the spacing the span ends with; every other byte
/// of it goes, as a plain removal takes it.
fn removed_keeping_comments(original: &str, start: usize, end: usize) -> Option<String> {
    let blank = |text: &str| {
        text.len()
            - text
                .trim_end_matches(|c: char| c.is_ascii_whitespace())
                .len()
    };
    let mut kept = String::new();
    let mut at = start;
    for (from, to) in read::spans(original) {
        if from < start || to > end {
            continue;
        }
        let before = original.get(at..from)?;
        kept.push_str(&before[before.len() - blank(before)..]);
        kept.push_str(original.get(from..to)?);
        at = to;
        if original[from..to].starts_with("//") {
            let line_end = ["\r\n", "\n", "\r"].into_iter().find(|t| {
                original
                    .get(at..end)
                    .is_some_and(|rest| rest.starts_with(t))
            });
            if let Some(line_end) = line_end {
                kept.push_str(line_end);
                at += line_end.len();
            }
        }
    }
    if at > start {
        let rest = original.get(at..end)?;
        kept.push_str(&rest[rest.len() - blank(rest)..]);
    }
    Some(format!(
        "{}{kept}{}",
        original.get(..start)?,
        original.get(end..)?
    ))
}

/// The edit made on the blanked text, put into the original at the same
/// bytes: the region where the two blanked texts differ.
fn splice(original: &str, before: &str, after: &str) -> Option<String> {
    let prefix = before
        .bytes()
        .zip(after.bytes())
        .take_while(|(a, b)| a == b)
        .count();
    let suffix = before.as_bytes()[prefix..]
        .iter()
        .rev()
        .zip(after.as_bytes()[prefix..].iter().rev())
        .take_while(|(a, b)| a == b)
        .count();
    let (start, end) = (prefix, before.len() - suffix);
    let replacement = after.get(prefix..after.len() - suffix)?;
    let removed = original.get(start..end)?;
    // Only bytes outside comments change: the original holds them as-is.
    (removed == before.get(start..end)?)
        .then(|| format!("{}{replacement}{}", &original[..start], &original[end..]))
}

/// Whether the old key of a JSON file's servers member can be moved (or
/// removed, when `remove`) in place now.
pub(super) fn json_editable(container: &Container, member: &str, remove: bool) -> bool {
    use crate::clients::naming::{NEW, OLD};
    json_edit(container, member, OLD, NEW, remove).is_ok()
}

pub(super) fn json_file(
    container: &Container,
    member: &str,
    from: &str,
    to: &str,
    remove: bool,
) -> Result<(), Failure> {
    let (read, edited) = json_edit(container, member, from, to, remove)?;
    files::write_text(&container.file, &read.with_bom(&edited))
}

/// Whether a step can be made, checked when planning (finding 2): the asset
/// key of the reason when it cannot, so the client is left alone.
pub(super) fn check(step: &super::Step, from: &str, to: &str) -> Result<(), &'static str> {
    use super::places::Kind;
    let container = &step.container;
    let path = &container.file;
    if files::unwritable(path).is_some() {
        return Err("unwritable");
    }
    let reason = |failure: Failure| {
        if failure.message.contains("not valid UTF-8") {
            "not_utf8"
        } else {
            "not_editable"
        }
    };
    match &container.kind {
        Kind::Claude(super::claude_cli::Scope::Local(project))
            if !super::claude_cli::addressable(project) =>
        {
            let at = jsonedit::join(&["projects", project, "mcpServers"]);
            let document = read::file(path)
                .and_then(|r| r.json())
                .ok_or("not_editable")?;
            document.pointer(&at).map(|_| ()).ok_or("not_editable")
        }
        Kind::Claude(_) => Ok(()),
        Kind::Json(member) => json_edit(container, member, from, to, step.remove_only)
            .map(|_| ())
            .map_err(reason),
        Kind::Toml if step.remove_only => Err("toml_both"),
        Kind::Toml => {
            let current = std::fs::read_to_string(path).map_err(|_| "not_editable")?;
            toml_key::moved(&current, from, to)
                .map(|_| ())
                .map_err(|_| "not_editable")
        }
    }
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
