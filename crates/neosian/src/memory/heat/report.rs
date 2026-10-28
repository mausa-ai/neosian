//! `memory heat` (DESIGN §40.33): the read documents under a path, most
//! read first, then how many were never read. An inspection surface over
//! the authorized listing joined with the local log: a row for a document
//! the listing does not show is never shown, so authority and policy
//! decide what is visible and heat only orders it.

use std::cmp::Reverse;
use std::io::Write;

use serde_json::json;

use super::{Counts, counts, directory};
use crate::memory::dispatch::Memory;
use crate::memory::index::count;
use crate::memory::mounts::{Mount, resolve, state_path};
use crate::store::Entry;
use crate::store::error::Error;
use crate::store::time::Stamp;

struct Read<'a> {
    mount: &'a Mount,
    entry: Entry,
    counts: Counts,
}

impl Read<'_> {
    fn shown(&self) -> String {
        format!("/{}/{}", self.mount.path, self.entry.path)
    }
}

/// Is `path` the document `prefix` names, or inside that directory?
fn under(path: &str, prefix: &str) -> bool {
    prefix.is_empty() || path == prefix || path.starts_with(&format!("{prefix}/"))
}

pub fn run(
    memory: &Memory,
    path: &str,
    limit: usize,
    json: bool,
    out: &mut impl Write,
) -> Result<u8, Error> {
    let now = Stamp::now();
    let (selected, prefix) = if path.trim_matches('/').is_empty() {
        (None, "")
    } else {
        let (mount, document) = resolve(&memory.mounts, path)?;
        (Some(mount), document)
    };
    let mut read = Vec::new();
    let mut unread = 0;
    for mount in &memory.mounts {
        if selected.is_some_and(|chosen| chosen.path != mount.path) {
            continue;
        }
        let mut heat = counts(&mount.scope, now);
        for entry in memory.store.list_documents(&mount.scope, prefix)? {
            if entry.redacted || state_path(&entry.path) || !under(&entry.path, prefix) {
                continue;
            }
            match heat.remove(&entry.path) {
                Some(counts) if counts.total > 0 => read.push(Read {
                    mount,
                    entry,
                    counts,
                }),
                _ => unread += 1,
            }
        }
    }
    read.sort_by_key(|read| {
        (
            Reverse(read.counts.month),
            Reverse(read.counts.total),
            Reverse(read.counts.last.map_or(i64::MIN, Stamp::instant)),
            read.shown(),
        )
    });
    let total = read.len();
    read.truncate(limit);
    let enabled = directory().is_some();
    if json {
        let documents: Vec<_> = read
            .iter()
            .map(|read| {
                json!({
                    "path": read.shown(),
                    "scope": read.mount.scope,
                    "sessions_7d": read.counts.week,
                    "sessions_30d": read.counts.month,
                    "sessions": read.counts.total,
                    "last_read": read.counts.last.map(|last| last.utc().z()),
                })
            })
            .collect();
        let envelope = json!({"path": path, "enabled": enabled, "documents": documents,
            "read": total, "unread": unread, "truncated": total > read.len()});
        writeln!(out, "{envelope}")?;
        return Ok(0);
    }
    writeln!(
        out,
        "heat under {path}: sessions that read each document (7d / 30d / all, last read)"
    )?;
    let width = read
        .iter()
        .map(|read| read.shown().chars().count())
        .max()
        .unwrap_or(0);
    for read in &read {
        let last = read
            .counts
            .last
            .map_or_else(String::new, |last| last.utc().z());
        writeln!(
            out,
            "- {:<width$}  {} / {} / {}  {}",
            read.shown(),
            read.counts.week,
            read.counts.month,
            read.counts.total,
            &last[..last.len().min(10)],
        )?;
    }
    if total > read.len() {
        writeln!(out, "({} more read; raise --limit)", total - read.len())?;
    }
    writeln!(out, "{} never read", count(unread))?;
    if !enabled {
        writeln!(out, "(heat log off: {}=off)", super::SWITCH_ENV)?;
    }
    Ok(0)
}
