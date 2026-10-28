//! `memory heat PATH --related` (DESIGN §40.34, #408): the documents read
//! by the sessions that read one document, each beside how many sessions
//! read it at all. Derived from the heat log alone: a row is one session
//! reading one path, and one server writes the same reader id to every
//! scope's file, so the join crosses mounts. Folded rows carry no reader,
//! so every number counts the verbatim rows only (at least the last
//! month): one population for all four. Raw co-reading favors documents
//! every session opens, so each row shows its own reach, and on equal
//! counts the narrower document comes first. Report only, joined with the
//! authorized listing like heat.

use std::collections::{BTreeMap, HashSet};
use std::io::Write;

use serde_json::json;

use super::{directory, reads_in};
use crate::memory::commands::document_at;
use crate::memory::dispatch::Memory;
use crate::memory::mounts::state_path;
use crate::store::error::{Error, MemoryError};

/// Per path: distinct readers among `with`, and distinct readers in all.
pub(super) fn tally<'a>(
    rows: &'a [(String, String)],
    with: &HashSet<&str>,
) -> BTreeMap<&'a str, (u64, u64)> {
    let mut seen = HashSet::new();
    let mut counts: BTreeMap<&str, (u64, u64)> = BTreeMap::new();
    for (reader, path) in rows {
        if seen.insert((reader.as_str(), path.as_str())) {
            let count = counts.entry(path.as_str()).or_default();
            count.0 += u64::from(with.contains(reader.as_str()));
            count.1 += 1;
        }
    }
    counts
}

struct Related {
    shown: String,
    scope: String,
    shared: u64,
    sessions: u64,
}

pub fn run(
    memory: &Memory,
    path: &str,
    limit: usize,
    json: bool,
    out: &mut impl Write,
) -> Result<u8, Error> {
    let (mount, document) = document_at(memory, path)?;
    if memory.store.read(&mount.scope, document)?.is_none() {
        return Err(MemoryError::not_found(&mount.scope, document).into());
    }
    let heat = directory();
    let mut loaded: BTreeMap<&str, Vec<(String, String)>> = BTreeMap::new();
    for other in &memory.mounts {
        loaded.entry(other.scope.as_str()).or_insert_with(|| {
            heat.as_deref()
                .map(|directory| reads_in(directory, &other.scope))
                .unwrap_or_default()
        });
    }
    let observed: HashSet<&str> = loaded
        .values()
        .flatten()
        .map(|(reader, _)| reader.as_str())
        .collect();
    let with: HashSet<&str> = loaded[mount.scope.as_str()]
        .iter()
        .filter(|(_, read)| read == document)
        .map(|(reader, _)| reader.as_str())
        .collect();
    let tallies: BTreeMap<&str, _> = loaded
        .iter()
        .map(|(scope, rows)| (*scope, tally(rows, &with)))
        .collect();
    let mut related = Vec::new();
    for other in &memory.mounts {
        let counts = &tallies[other.scope.as_str()];
        for entry in memory.store.list_documents(&other.scope, "")? {
            let itself = other.scope == mount.scope && entry.path == document;
            if entry.redacted || state_path(&entry.path) || itself {
                continue;
            }
            if let Some(&(shared, sessions)) = counts.get(entry.path.as_str())
                && shared > 0
            {
                related.push(Related {
                    shown: format!("/{}/{}", other.path, entry.path),
                    scope: other.scope.clone(),
                    shared,
                    sessions,
                });
            }
        }
    }
    related.sort_by(|a, b| {
        (b.shared.cmp(&a.shared))
            .then(a.sessions.cmp(&b.sessions))
            .then(a.shown.cmp(&b.shown))
    });
    let total = related.len();
    related.truncate(limit);
    if json {
        let rows: Vec<_> = related
            .iter()
            .map(|r| json!({"path": r.shown, "scope": r.scope, "shared": r.shared, "sessions": r.sessions}))
            .collect();
        let envelope = json!({"path": path, "enabled": heat.is_some(), "sessions": with.len(),
            "observed": observed.len(), "related": rows, "truncated": total > related.len()});
        writeln!(out, "{envelope}")?;
        return Ok(0);
    }
    writeln!(
        out,
        "related to {path}: read by {} of {} logged sessions; what they also read \
         (shared / read by)",
        with.len(),
        observed.len()
    )?;
    let width = related
        .iter()
        .map(|r| r.shown.chars().count())
        .max()
        .unwrap_or(0);
    for r in &related {
        writeln!(out, "- {:<width$}  {} / {}", r.shown, r.shared, r.sessions)?;
    }
    if total > related.len() {
        writeln!(
            out,
            "({} more related; raise --limit)",
            total - related.len()
        )?;
    }
    if with.is_empty() {
        writeln!(out, "no session has read it")?;
    } else if total == 0 {
        writeln!(out, "its sessions read no other document")?;
    }
    if heat.is_none() {
        writeln!(out, "(heat log off: {}=off)", super::SWITCH_ENV)?;
    }
    Ok(0)
}
