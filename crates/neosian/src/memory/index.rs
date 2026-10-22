//! The memory index (`memory/index.py`, DESIGN §8): every mount and its
//! documents as markdown, the one rendering `view /` serves and a session
//! is opened with. At scale it pages instead of growing: under the budget
//! every document keeps its line; over it the least recently updated fold
//! into per-directory counts, and a mount that cannot hold even its folds
//! collapses to its total. A folded region is read back with `view` of the
//! directory: paging, never deletion.

use std::cmp::Reverse;
use std::collections::{BTreeMap, HashSet};

use super::mounts::{Mount, state_path};
use crate::assets::{MESSAGES, block};
use crate::state::State;
use crate::store::Entry;
use crate::store::error::Error;

/// About 2k tokens under the §6 character heuristic.
const BUDGET_CHARS: isize = 8192;

type Listing<'a> = (&'a Mount, Vec<Entry>);

/// Python's `len`: characters, never bytes (a header carries an em dash).
fn chars(text: &str) -> isize {
    text.chars().count() as isize
}

pub fn render(store: &dyn State, mounts: &[Mount]) -> Result<String, Error> {
    render_within(store, mounts, BUDGET_CHARS)
}

/// The index under `budget` characters: the session start block's share.
pub fn render_within(store: &dyn State, mounts: &[Mount], budget: isize) -> Result<String, Error> {
    let mut listings: Vec<Listing> = Vec::new();
    let mut suffix = String::new();
    let template = block(MESSAGES, "index").expect("messages.yaml carries the index line");
    for mount in mounts {
        let mut entries = store.list_documents(&mount.scope, "")?;
        let mailbox = entries
            .iter()
            .filter(|entry| entry.path.starts_with("messages/") && !entry.redacted)
            .count();
        if mailbox > 0 {
            let line = template
                .replace("{{mount}}", &mount.path)
                .replace("{{count}}", &mailbox.to_string());
            suffix = format!("{suffix}\n{}", line.trim_end());
        }
        entries.retain(|entry| !state_path(&entry.path));
        listings.push((mount, entries));
    }
    let budget = budget - chars(&suffix);
    let full = blocks(&listings, None);
    let body = if chars(&full) <= budget {
        full
    } else {
        tiered(&listings, budget).unwrap_or_else(|| floor(&listings))
    };
    Ok(body + &suffix)
}

fn header(mount: &Mount) -> String {
    let mut header = format!("## /{}", mount.path);
    if !mount.description.is_empty() {
        header = format!("{header} — {}", mount.description);
    }
    if mount.read_only {
        header += " (read-only)";
    }
    if mount.edit_only {
        header += " (edit-only)";
    }
    header
}

/// `1 document`, `7 documents`.
pub fn count(documents: usize) -> String {
    let plural = if documents == 1 { "" } else { "s" };
    format!("{documents} document{plural}")
}

fn doc_line(mount: &Mount, entry: &Entry) -> String {
    let marker = if entry.redacted { " (redacted)" } else { "" };
    format!("- /{}/{}{marker}", mount.path, entry.path)
}

fn fold_line(mount: &Mount, segment: &str, documents: usize) -> String {
    format!("- /{}/{segment}/ ({})", mount.path, count(documents))
}

fn loose_line(mount: &Mount, documents: usize) -> String {
    format!("- /{}/ ({documents} more)", mount.path)
}

/// The directory a document folds into; None for one at the mount's root.
fn directory(entry: &Entry) -> Option<&str> {
    entry.path.split_once('/').map(|(segment, _)| segment)
}

/// What stays out of the hot set, per mount: documents per top-level
/// directory, and the loose ones at the root.
fn folds(entries: &[Entry], hot: impl Fn(&Entry) -> bool) -> (BTreeMap<&str, usize>, usize) {
    let mut segments = BTreeMap::new();
    let mut loose = 0;
    for entry in entries.iter().filter(|entry| !hot(entry)) {
        match directory(entry) {
            Some(segment) => *segments.entry(segment).or_insert(0) += 1,
            None => loose += 1,
        }
    }
    (segments, loose)
}

/// One block per mount: the hot documents' lines, then the folds.
/// `hot = None` keeps every line, the rendering under the budget.
fn blocks(listings: &[Listing], hot: Option<&HashSet<(usize, &str)>>) -> String {
    let mut blocks = Vec::new();
    for (index, (mount, entries)) in listings.iter().enumerate() {
        let is_hot =
            |entry: &Entry| hot.is_none_or(|hot| hot.contains(&(index, entry.path.as_str())));
        let mut lines = vec![header(mount)];
        if entries.is_empty() {
            lines.push("(empty)".to_string());
        }
        lines.extend(
            entries
                .iter()
                .filter(|entry| is_hot(entry))
                .map(|entry| doc_line(mount, entry)),
        );
        let (segments, loose) = folds(entries, is_hot);
        lines.extend(
            segments
                .iter()
                .map(|(segment, documents)| fold_line(mount, segment, *documents)),
        );
        if loose > 0 {
            lines.push(loose_line(mount, loose));
        }
        blocks.push(lines.join("\n"));
    }
    blocks.join("\n\n")
}

/// The warm tier, or None when even it overruns: documents are promoted
/// onto their own lines newest first, and promotion stops at the first
/// that no longer fits (a recency cutoff, never a knapsack). A promotion
/// adds a line and shrinks or removes its fold, so it may reduce the
/// total; one that fits or shrinks is taken.
fn tiered(listings: &[Listing], budget: isize) -> Option<String> {
    let mut documents: Vec<(usize, &Entry)> = listings
        .iter()
        .enumerate()
        .flat_map(|(index, (_, entries))| entries.iter().map(move |entry| (index, entry)))
        .collect();
    documents.sort_by_key(|(_, entry)| Reverse(entry.updated_at.instant()));
    let mut cold: Vec<_> = listings
        .iter()
        .map(|(_, entries)| folds(entries, |_| false))
        .collect();
    let mut hot = HashSet::new();
    let mut length = chars(&blocks(listings, Some(&hot)));
    for (index, entry) in documents {
        let mount = listings[index].0;
        let (segments, loose) = &mut cold[index];
        let (left, before, after) = match directory(entry) {
            Some(segment) => {
                let left = segments.entry(segment).or_default();
                let before = fold_line(mount, segment, *left);
                let after = fold_line(mount, segment, *left - 1);
                (left, before, after)
            }
            None => {
                let before = loose_line(mount, *loose);
                let after = loose_line(mount, *loose - 1);
                (loose, before, after)
            }
        };
        let folded = if *left == 1 {
            chars(&before) + 1
        } else {
            chars(&before) - chars(&after)
        };
        let delta = chars(&doc_line(mount, entry)) + 1 - folded;
        if length + delta > budget && delta >= 0 {
            break;
        }
        length += delta;
        *left -= 1;
        hot.insert((index, entry.path.as_str()));
    }
    (length <= budget).then(|| blocks(listings, Some(&hot)))
}

/// The cold tier: every mount collapsed to its total, returned even when
/// it still overruns the budget.
fn floor(listings: &[Listing]) -> String {
    let block = |(mount, entries): &Listing| {
        let total = if entries.is_empty() {
            "(empty)".to_string()
        } else {
            format!("({} — view /{}/ to list)", count(entries.len()), mount.path)
        };
        format!("{}\n{total}", header(mount))
    };
    listings.iter().map(block).collect::<Vec<_>>().join("\n\n")
}

#[cfg(test)]
mod tests;
