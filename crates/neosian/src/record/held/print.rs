//! What `neosian record held` prints (`record/held.rs`). Plain output
//! names each turn by its ID, with its spool tree where the same ID is
//! listed from more than one tree. `--json` prints one object: a listing
//! as `{"held": [...], "unserved": [...]}`, each entry with `id`, `spool`,
//! `closed_at`, `bytes`, `destination` and, for a held turn, `reason`,
//! `status`, `code` and `message`; a discard or a retry as `{"action",
//! "turns": [{"id", "spool", "outcome", "bytes", "queued", "why"}],
//! "done": {"turns", "bytes"}}`. A reason's message is shown as the lander
//! kept it, on one line.

use std::io::{self, Write};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use super::super::landing::aside::Entry;

/// One turn's outcome.
pub struct Row {
    pub tree: PathBuf,
    pub id: String,
    pub outcome: &'static str,
    /// The bytes of a turn put back or deleted; `None` while it stays held.
    pub bytes: Option<u64>,
    /// The ID a retried turn has in the queue.
    pub queued: Option<String>,
    pub why: Option<String>,
}

impl Row {
    pub fn new(tree: &Path, id: String) -> Self {
        Self {
            tree: tree.to_path_buf(),
            id,
            outcome: "",
            bytes: None,
            queued: None,
            why: None,
        }
    }
}

/// Bytes as a person reads them: B, KiB, MiB or GiB, one decimal.
fn size(bytes: u64) -> String {
    let units = [("GiB", 1u64 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)];
    match units.iter().find(|(_, unit)| bytes >= *unit) {
        Some((name, unit)) => format!("{:.1} {name}", bytes as f64 / *unit as f64),
        None => format!("{bytes} B"),
    }
}

/// A message on one line: control characters become spaces.
fn line(text: &str) -> String {
    let flat = |c: char| if c.is_control() { ' ' } else { c };
    text.chars().map(flat).collect()
}

/// How plain output names `id` in `tree`: with its tree when `all` lists
/// the same ID from another tree.
fn named(tree: &Path, id: &str, all: &[(&Path, &str)]) -> String {
    match all.iter().filter(|(_, other)| *other == id).count() > 1 {
        true => format!("{id} in {}", tree.display()),
        false => id.to_string(),
    }
}

fn plural(count: usize) -> &'static str {
    if count == 1 { "turn" } else { "turns" }
}

/// The held turns and the pending turns no environment here serves.
pub fn list(kept: &[Entry], waiting: &[Entry], json: bool, out: &mut impl Write) -> io::Result<()> {
    let row = |entry: &Entry| {
        json!({"id": entry.name(), "spool": entry.tree, "closed_at": entry.closed_at,
               "bytes": entry.bytes, "destination": entry.destination})
    };
    if json {
        let held: Vec<Value> = kept
            .iter()
            .map(|entry| {
                let said = entry.said.clone().unwrap_or_default();
                let mut row = row(entry);
                row["reason"] = json!(Some(said.reason).filter(|reason| !reason.is_empty()));
                row["status"] = json!(said.status);
                row["code"] = json!(said.code);
                row["message"] = json!(said.message);
                row
            })
            .collect();
        let unserved: Vec<Value> = waiting.iter().map(row).collect();
        return writeln!(out, "{}", json!({"held": held, "unserved": unserved}));
    }
    let names: Vec<(String, &Entry)> = kept.iter().chain(waiting).map(|e| (e.name(), e)).collect();
    let all: Vec<(&Path, &str)> = names
        .iter()
        .map(|(n, e)| (e.tree.as_path(), n.as_str()))
        .collect();
    let total = |entries: &[Entry]| size(entries.iter().map(|entry| entry.bytes).sum());
    let head = |entry: &Entry, name: &str| {
        let at = entry.closed_at.as_deref().unwrap_or("at an unknown time");
        let name = named(&entry.tree, name, &all);
        format!("  {name}  closed {at}  {}", size(entry.bytes))
    };
    match kept.len() {
        0 => writeln!(out, "No turn is held.")?,
        count => writeln!(
            out,
            "Held turns ({count}, {}); nothing sends them again by itself:",
            total(kept)
        )?,
    }
    for (name, entry) in &names[..kept.len()] {
        let said = entry.said.clone().unwrap_or_default();
        let mut why = match said.reason.as_str() {
            "" => "no readable reason".to_string(),
            reason => reason.to_string(),
        };
        let answered = [said.status.map(|status| status.to_string()), said.code];
        let answered: Vec<String> = answered.into_iter().flatten().collect();
        if !answered.is_empty() {
            why = format!("{why} ({})", answered.join(" "));
        }
        if let Some(message) = said.message {
            why = format!("{why}: {}", line(&message));
        }
        writeln!(out, "{}  {why}", head(entry, name))?;
    }
    if !waiting.is_empty() {
        writeln!(
            out,
            "Pending turns this environment does not serve ({}, {}); a hook whose \
             environment names their destination lands them:",
            waiting.len(),
            total(waiting)
        )?;
    }
    for (name, entry) in &names[kept.len()..] {
        let to = entry.destination.as_deref();
        let to = to.unwrap_or("a destination its state does not name");
        writeln!(out, "{}  for {to}", head(entry, name))?;
    }
    Ok(())
}

/// What a discard or a retry did with each turn, and in all.
pub fn outcomes(verb: &str, rows: &[Row], json: bool, out: &mut impl Write) -> io::Result<()> {
    let done: Vec<u64> = rows.iter().filter_map(|row| row.bytes).collect();
    let bytes: u64 = done.iter().sum();
    if json {
        let turns: Vec<Value> = rows
            .iter()
            .map(|row| {
                json!({"id": row.id, "spool": row.tree, "outcome": row.outcome,
                       "bytes": row.bytes, "queued": row.queued, "why": row.why})
            })
            .collect();
        let done = json!({"turns": done.len(), "bytes": bytes});
        return writeln!(
            out,
            "{}",
            json!({"action": verb, "turns": turns, "done": done})
        );
    }
    let all: Vec<(&Path, &str)> = rows
        .iter()
        .map(|r| (r.tree.as_path(), r.id.as_str()))
        .collect();
    for row in rows {
        let what = match (row.bytes, &row.queued) {
            (Some(bytes), Some(queued)) => format!("put back as {queued} ({})", size(bytes)),
            (Some(bytes), None) => format!("discarded ({})", size(bytes)),
            (None, _) => format!(
                "stays held ({}): {}",
                row.outcome,
                line(row.why.as_deref().unwrap_or_default())
            ),
        };
        writeln!(out, "{}: {what}", named(&row.tree, &row.id, &all))?;
    }
    let total = format!("{} {}, {}", done.len(), plural(done.len()), size(bytes));
    match verb {
        "discard" => writeln!(out, "Discarded {total}.")?,
        _ => writeln!(
            out,
            "Put back {total}. Nothing is sent now: the next hook event that starts a \
             lander for a turn's destination lands it."
        )?,
    }
    let stayed = rows.len() - done.len();
    match stayed {
        0 => {}
        1 => writeln!(out, "1 turn stays held.")?,
        _ => writeln!(out, "{stayed} turns stay held.")?,
    }
    Ok(())
}
