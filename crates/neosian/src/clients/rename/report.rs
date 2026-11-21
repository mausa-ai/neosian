//! What the rename says (DESIGN §40.53 item 4): which clients moved, each
//! allow entry that names the old tools, each mention that kept a client,
//! and that the old name keeps working. Paths, lines and keys; never values.

use super::{
    Stop,
    moves::{Outcome, State},
    servers, text,
};
use crate::clients::context::Context;
use serde_json::{Value, json};
use std::io::{self, Write};

fn stop(stop: &Stop) -> Value {
    let (kind, path, at) = match stop {
        Stop::Taken(p, at) => ("taken", Some(p), at.as_str()),
        Stop::Differ(p, at) => ("differ", Some(p), at.as_str()),
        Stop::Unreadable(p) => ("unreadable", Some(p), ""),
        Stop::Receipt(p) => ("receipt", Some(p), ""),
        Stop::Shared(p, other) => ("shared", Some(p), *other),
        Stop::Unedited(p) => ("unedited", Some(p), ""),
        Stop::Unwritable(p, key) => ("unwritable", Some(p), *key),
        Stop::Held => ("held", None, ""),
    };
    json!({"kind": kind, "path": path, "at": at})
}

/// The client's `rename` report member.
pub fn member(outcome: &Outcome) -> Value {
    let plan = &outcome.plan;
    let places = |allow: bool| {
        plan.found
            .mentions
            .iter()
            .filter(|m| m.allow == allow)
            .map(|m| json!({"path": m.path, "line": m.line}))
            .collect::<Vec<_>>()
    };
    let state = match outcome.state {
        State::Unchanged => "unchanged",
        State::Moved => "moved",
        State::Left => "left",
        State::Failed => "failed",
    };
    json!({"state": state, "error": outcome.error, "allows": places(true),
        "mentions": places(false), "unreadable": plan.found.unreadable,
        "stops": plan.stops.iter().map(stop).collect::<Vec<_>>(),
        "hosted": plan.hosted.iter().map(|(p, at)| json!({"path": p, "at": at})).collect::<Vec<_>>()})
}

/// The text lines of one client's outcome; nothing when nothing applied.
pub fn lines(outcome: &Outcome) -> Vec<String> {
    let plan = &outcome.plan;
    let label = plan.label;
    let mut lines = vec![];
    let at = |m: &super::Mention| format!("{}:{}", m.path.display(), m.line);
    match outcome.state {
        State::Unchanged => {}
        State::Moved => {
            lines.push(text::fill("moved", &[("label", label)]));
            if matches!(plan.client, "cursor" | "claude-desktop") {
                lines.push(format!(
                    "  {}",
                    text::fill("moved_app_state", &[("label", label)])
                ));
            }
            for m in plan.found.mentions.iter().filter(|m| m.allow) {
                lines.push(format!("  {}", text::fill("allow", &[("place", &at(m))])));
            }
        }
        State::Failed => {
            let error = outcome.error.clone().unwrap_or_default();
            lines.push(text::fill("failed", &[("label", label), ("error", &error)]));
        }
        State::Left => {
            lines.push(text::fill("left", &[("label", label)]));
            for s in &plan.stops {
                lines.push(format!("  {}", stop_line(s)));
            }
            for m in &plan.found.mentions {
                let key = if m.allow { "allow" } else { "mention" };
                lines.push(format!("  {}", text::fill(key, &[("place", &at(m))])));
            }
            for p in &plan.found.unreadable {
                let path = p.display().to_string();
                lines.push(format!(
                    "  {}",
                    text::fill("mention_unreadable", &[("path", &path)])
                ));
            }
            if plan.by_command() {
                lines.push(format!("  {}", text::get("move_anyway")));
            }
        }
    }
    for (path, pointer) in &plan.hosted {
        let place = format!("{} {pointer}", path.display());
        lines.push(format!("  {}", text::fill("hosted", &[("place", &place)])));
    }
    lines
}

fn stop_line(stop: &Stop) -> String {
    match stop {
        Stop::Taken(p, at) => text::fill("taken", &[("place", &format!("{} {at}", p.display()))]),
        Stop::Differ(p, at) => text::fill("differ", &[("place", &format!("{} {at}", p.display()))]),
        Stop::Unreadable(p) => text::fill("unreadable", &[("path", &p.display().to_string())]),
        Stop::Receipt(p) => text::fill("receipt", &[("path", &p.display().to_string())]),
        Stop::Shared(p, other) => text::fill(
            "shared",
            &[("path", &p.display().to_string()), ("other", other)],
        ),
        Stop::Unedited(p) => text::fill("unedited", &[("path", &p.display().to_string())]),
        Stop::Unwritable(p, key) => text::fill(key, &[("path", &p.display().to_string())]),
        Stop::Held => text::get("held"),
    }
}

/// Two of our servers for a client, as report members and text.
pub fn two(client: &'static str, context: &Context, only_here: bool) -> (Vec<Value>, Vec<String>) {
    let pairs = servers::two(client, context, only_here);
    let lines = pairs
        .iter()
        .map(|p| {
            let fix = match &p.project {
                Some(dir) => text::fill("two_fix_here", &[("dir", &dir.display().to_string())]),
                None => text::get("two_fix"),
            };
            text::fill("two", &[("old", &p.old), ("new", &p.new), ("fix", &fix)])
        })
        .collect();
    (pairs.iter().map(servers::Pair::report).collect(), lines)
}

pub fn undo_text(report: &Value, out: &mut impl Write) -> io::Result<()> {
    for row in report["clients"].as_array().into_iter().flatten() {
        let label = row["label"].as_str().unwrap_or("");
        if row["back"].is_number() {
            writeln!(out, "{}", text::fill("undone", &[("label", label)]))?;
        }
        if let Some(error) = row["error"].as_str() {
            writeln!(
                out,
                "{}",
                text::fill("failed", &[("label", label), ("error", error)])
            )?;
        }
        for place in row["refused"].as_array().into_iter().flatten() {
            let place = place.as_str().unwrap_or("");
            writeln!(out, "  {}", text::fill("undo_refused", &[("place", place)]))?;
        }
    }
    Ok(())
}
