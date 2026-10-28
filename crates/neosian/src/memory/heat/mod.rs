//! Read heat (DESIGN §40.33): which documents agents actually read. A
//! document body the MCP memory tool's `view` showed, or an included
//! knowledge `read` delivered, leaves one row per reader and path in a log
//! beside the store in the home, never in the store: format 1, wire 6,
//! archives, exchange and profiles are untouched, and a read never becomes
//! a write. The index, listings, mailbox documents, the shell, the
//! gardener, search, export and conformance leave no trace. The log is
//! derived state: `memory heat` reports it, `--related` joins it by
//! session (§40.34) and upkeep scan cites it; the index and search render
//! as they did without it. Every write here is best effort, since the read it
//! describes has already been answered.

pub mod related;
pub mod report;
mod rows;
#[cfg(test)]
mod tests;

use std::collections::{BTreeMap, HashMap, HashSet};
use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};

use rows::{Row, load};

use crate::home::home;
use crate::store::fileio::{append_line, atomic_write};
use crate::store::time::Stamp;

/// `NEOSIAN_HEAT=off` leaves the log unwritten; nothing else reads it.
pub const SWITCH_ENV: &str = "NEOSIAN_HEAT";
const DIRECTORY: &str = "heat";
const DAY: i64 = 86_400;
pub const WEEK_DAYS: i64 = 7;
pub const MONTH_DAYS: i64 = 30;
/// A file past this size folds its rows older than the month into one
/// total per path (§9.6's projection: verbatim, then folded).
const COMPACT_BYTES: u64 = 2 * 1024 * 1024;
/// The write-time set only spares repeated rows; the fold counts distinct
/// readers either way, so forgetting it past this size costs nothing.
const SEEN_CEILING: usize = 100_000;

/// The heat directory: `<home>/heat`, or None when the switch is off or
/// the home is unknown.
pub fn directory() -> Option<PathBuf> {
    if env::var(SWITCH_ENV).is_ok_and(|value| value.eq_ignore_ascii_case("off")) {
        return None;
    }
    home().ok().map(|home| home.join(DIRECTORY))
}

/// One file per scope, `:` and `/` percent-encoded (the grammar leaves
/// nothing else to encode).
pub fn file(directory: &Path, scope: &str) -> PathBuf {
    let name = scope.replace(':', "%3A").replace('/', "%2F");
    directory.join(format!("{name}.jsonl"))
}

/// A document's heat: distinct readers in the last week and month, in
/// all, and its last read.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub week: u64,
    pub month: u64,
    pub total: u64,
    pub last: Option<Stamp>,
}

fn later(held: Option<Stamp>, seen: Stamp) -> Option<Stamp> {
    match held {
        Some(held) if held.instant() >= seen.instant() => Some(held),
        _ => Some(seen),
    }
}

/// Every read document of `scope` with its counts, from the file under
/// `directory`: distinct readers per window, folds counting toward the
/// total and the last read alone.
pub fn counts_in(directory: &Path, scope: &str, now: Stamp) -> BTreeMap<String, Counts> {
    let week = now.after(-WEEK_DAYS * DAY).instant();
    let month = now.after(-MONTH_DAYS * DAY).instant();
    let mut found: BTreeMap<String, Counts> = BTreeMap::new();
    let mut readers: HashMap<(String, String), i64> = HashMap::new();
    for row in load(&file(directory, scope)).unwrap_or_default() {
        match row {
            Row::Read { at, reader, path } => {
                let latest = readers.entry((path, reader)).or_insert(i64::MIN);
                *latest = (*latest).max(at.instant());
            }
            Row::Fold {
                path,
                readers,
                last,
            } => {
                let counts = found.entry(path).or_default();
                counts.total += readers;
                counts.last = later(counts.last, last);
            }
        }
    }
    for ((path, _), at) in readers {
        let counts = found.entry(path).or_default();
        counts.total += 1;
        counts.month += u64::from(at >= month);
        counts.week += u64::from(at >= week);
        counts.last = later(counts.last, Stamp::from_micros(at));
    }
    found
}

/// The verbatim rows of `scope` as (reader, path) pairs: what co-access
/// joins across scopes by reader. Folds carry no reader and are skipped.
pub fn reads_in(directory: &Path, scope: &str) -> Vec<(String, String)> {
    load(&file(directory, scope))
        .unwrap_or_default()
        .into_iter()
        .filter_map(|row| match row {
            Row::Read { reader, path, .. } => Some((reader, path)),
            Row::Fold { .. } => None,
        })
        .collect()
}

/// `counts_in` over the home's heat directory; empty when the log is off.
pub fn counts(scope: &str, now: Stamp) -> BTreeMap<String, Counts> {
    directory().map_or_else(BTreeMap::new, |directory| counts_in(&directory, scope, now))
}

/// The write side: one server's set of the reads it has already logged.
#[derive(Default)]
pub struct Heat {
    seen: Mutex<HashSet<(String, String, String)>>,
}

impl Heat {
    /// `reader` read `path` in `scope`: one row per reader and path.
    pub fn observe(&self, reader: &str, scope: &str, path: &str) {
        if let Some(directory) = directory() {
            self.observe_in(&directory, reader, scope, path, Stamp::now());
        }
    }

    pub fn observe_in(&self, directory: &Path, reader: &str, scope: &str, path: &str, at: Stamp) {
        let key = (reader.to_string(), scope.to_string(), path.to_string());
        {
            let mut seen = self.seen.lock().unwrap_or_else(PoisonError::into_inner);
            if seen.len() >= SEEN_CEILING {
                seen.clear();
            }
            if !seen.insert(key) {
                return;
            }
        }
        let file = file(directory, scope);
        let row = Row::Read {
            at,
            reader: reader.to_string(),
            path: path.to_string(),
        };
        if append_line(&file, &row.line(), false).is_ok()
            && fs::metadata(&file).is_ok_and(|meta| meta.len() > COMPACT_BYTES)
        {
            let _ = compact(&file, at);
        }
    }
}

/// Rows older than the month fold into one total per path; the rest stay
/// verbatim, so the windows never lose a row they count.
fn compact(file: &Path, now: Stamp) -> io::Result<()> {
    let month = now.after(-MONTH_DAYS * DAY).instant();
    let mut folds: BTreeMap<String, (HashSet<String>, u64, Stamp)> = BTreeMap::new();
    let mut kept = Vec::new();
    for row in load(file)? {
        match row {
            Row::Read { at, reader, path } if at.instant() < month => {
                let fold = folds.entry(path).or_insert((HashSet::new(), 0, at));
                fold.0.insert(reader);
                fold.2 = later(Some(fold.2), at).expect("one of two stamps");
            }
            Row::Fold {
                path,
                readers,
                last,
            } => {
                let fold = folds.entry(path).or_insert((HashSet::new(), 0, last));
                fold.1 += readers;
                fold.2 = later(Some(fold.2), last).expect("one of two stamps");
            }
            row => kept.push(row),
        }
    }
    let folded = folds
        .into_iter()
        .map(|(path, (readers, counted, last))| Row::Fold {
            path,
            readers: readers.len() as u64 + counted,
            last,
        });
    let text: String = folded.chain(kept).map(|row| row.line()).collect();
    atomic_write(file, &text, false)
}

/// The file rewritten through `keep`; a file that does not exist is left so.
fn rewrite(file: &Path, keep: impl FnMut(Row) -> Option<Row>) -> io::Result<()> {
    if !file.exists() {
        return Ok(());
    }
    let text: String = load(file)?
        .into_iter()
        .filter_map(keep)
        .map(|row| row.line())
        .collect();
    atomic_write(file, &text, false)
}

/// A deleted or redacted document's rows leave the log.
pub fn forget(scope: &str, path: &str) {
    if let Some(directory) = directory() {
        let _ = forget_in(&directory, scope, path);
    }
}

pub fn forget_in(directory: &Path, scope: &str, path: &str) -> io::Result<()> {
    rewrite(&file(directory, scope), |row| {
        (row.path() != path).then_some(row)
    })
}

/// A redacted scope's log is removed whole.
pub fn forget_scope(scope: &str) {
    if let Some(directory) = directory() {
        let file = file(&directory, scope);
        if file.exists() {
            let _ = fs::remove_file(file);
        }
    }
}

/// A renamed document keeps its heat: its rows follow it, across scopes
/// when the move crossed mounts.
pub fn rename(scope: &str, old: &str, new_scope: &str, new: &str) {
    if let Some(directory) = directory() {
        let _ = rename_in(&directory, scope, old, new_scope, new);
    }
}

pub fn rename_in(
    directory: &Path,
    scope: &str,
    old: &str,
    new_scope: &str,
    new: &str,
) -> io::Result<()> {
    let mut moved = Vec::new();
    rewrite(&file(directory, scope), |row| {
        if row.path() == old {
            moved.push(row.relabel(new));
            None
        } else {
            Some(row)
        }
    })?;
    let text: String = moved.iter().map(Row::line).collect();
    if text.is_empty() {
        return Ok(());
    }
    append_line(&file(directory, new_scope), &text, false)
}
