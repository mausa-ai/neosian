//! The keep limit (DESIGN §40.54 items 17.7 and 23; ruling D12 of the
//! T1e1 plan). `[record] keep_bytes` in the home's
//! `config.toml` bounds the bytes of every open span, pending turn and held
//! turn in the home's spool trees (`spool/`, `profile-spool/`, and the tree
//! of a hook given `--spool DIR`), counted as their `.jsonl` files. Without
//! the setting the limit is 512 MiB (item 23, owner decision O9); nothing
//! turns it off, a larger value raises it. A value that is not an integer
//! from 16 MiB to 64 GiB refuses the hook event that reads it, before its
//! record is written.
//!
//! Every record a hook appends is checked first, by a scan of the trees
//! (`usage.rs`; D12 caches no total). When the record would pass the
//! limit, closed turns, pending or held, are deleted in the order they
//! closed until it fits, by each turn's `closed_at`, within a conversation
//! and across conversations alike. A conversation's numbers follow that
//! order, so its turns go by number and one state is read for each turn
//! next in line; a conversation with a held turn or a turn put back by
//! `record held --retry` (which takes a later number, keeps its
//! `closed_at` and keeps its reason file beside it) is ordered by every
//! turn's `closed_at` instead, so a new number never decides when a turn
//! goes.
//! A conversation whose landing lock another process holds is skipped
//! whole, and the lock is held while its turns are deleted, so a lander
//! never loses a turn it is sending. When nothing more can go the
//! record is still appended, and the next one checks again. When the open
//! span the record belongs to passes the limit by itself, the span is
//! deleted, and the rest of that turn is dropped up to the conversation's
//! next prompt, which starts a new span: `<id>.dropped` beside the span
//! marks it. Every deleted turn and byte is counted in `drops.rs` before
//! its files go.

use std::io;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::destination::stamp;
use super::draft::micros_z;
use super::drops;
use super::lock::tidy;
use super::queue::Queue;
use super::slots;
use super::state::State;
use super::usage::{Closed, Conversation, scan, stat, trees};
use crate::record::payload::Record;
use crate::record::settings::Settings;
use crate::record::spool::{Spool, line};
use crate::store::fileio::{atomic_write, private_mkdir, remove_file};
use crate::store::time::Stamp;

/// The smallest, the default and the largest `[record] keep_bytes` (item 23).
pub const FLOOR: u64 = 16 << 20;
pub const DEFAULT: u64 = 512 << 20;
pub const CEILING: u64 = 64 << 30;
const SETTING: &str = "[record] keep_bytes";

/// `[record] keep_bytes` of the home at `home`, `DEFAULT` when it is not
/// set or `config.toml` is missing or is not TOML.
pub fn limit(home: &Path) -> io::Result<u64> {
    let Ok(document) = crate::config::read(&home.join("config.toml")) else {
        return Ok(DEFAULT);
    };
    let Some(value) = document
        .get("record")
        .and_then(|record| record.get("keep_bytes"))
    else {
        return Ok(DEFAULT);
    };
    value
        .as_integer()
        .and_then(|bytes| u64::try_from(bytes).ok())
        .filter(|bytes| (FLOOR..=CEILING).contains(bytes))
        .ok_or_else(|| {
            io::Error::other(format!(
                "{SETTING} must be an integer in {FLOOR}..{CEILING}"
            ))
        })
}

/// A record about to be appended to conversation `id`'s open span in the
/// spool tree `tree` of the home at `home`: whether it opens a turn (a
/// prompt) and its bytes as the spool writes them.
pub struct Arrival<'a> {
    pub home: &'a Path,
    pub tree: &'a Path,
    pub id: &'a str,
    pub prompt: bool,
    pub bytes: u64,
}

/// Appends `record` to the open span of conversation `id` when the keep
/// limit lets it; whether it was appended.
pub fn append(settings: &Settings, spool: &Spool, id: &str, record: &Record) -> io::Result<bool> {
    let line = line(record);
    let kept = match home_of(&settings.spool) {
        Some(home) => {
            let prompt = record.get("kind").and_then(Value::as_str) == Some("prompt");
            let arrival = Arrival {
                home: &home,
                tree: &settings.spool,
                id,
                prompt,
                bytes: line.len() as u64,
            };
            admit(&arrival, Stamp::now())?
        }
        None => true,
    };
    if kept {
        spool.write(id, &line)?;
    }
    Ok(kept)
}

/// The home whose limit binds the spool tree `tree`: the parent of a
/// `spool/` or `profile-spool/`, else the neosian home.
fn home_of(tree: &Path) -> Option<PathBuf> {
    let own = tree
        .file_name()
        .is_some_and(|name| name == "spool" || name == "profile-spool");
    match tree.parent() {
        Some(parent) if own => Some(parent.to_path_buf()),
        _ => crate::home::home().ok(),
    }
}

/// Whether the record of `arrival` may be appended at `now`, after what the
/// limit asks is deleted and counted.
pub fn admit(arrival: &Arrival, now: Stamp) -> io::Result<bool> {
    let limit = limit(arrival.home)?;
    let landing = slots::directory(arrival.home);
    let marker = arrival.tree.join(format!("{}.dropped", arrival.id));
    if marker.try_exists()? {
        if !arrival.prompt {
            drops::add(&landing, 0, arrival.bytes, now)?;
            return Ok(false);
        }
        remove_file(&marker)?;
    }
    let trees = trees(arrival.home, arrival.tree);
    let (mut total, closed) = scan(&trees)?;
    if total + arrival.bytes <= limit {
        return Ok(true);
    }
    let span = arrival.tree.join(format!("{}.jsonl", arrival.id));
    let own = stat(&span)?.map_or(0, |metadata| metadata.len());
    if own > 0 && own + arrival.bytes > limit {
        if !arrival.prompt {
            drops::add(&landing, 1, own + arrival.bytes, now)?;
            remove_file(&span)?;
            atomic_write(&marker, "", false)?;
            return Ok(false);
        }
        drops::add(&landing, 1, own, now)?;
        remove_file(&span)?;
        total -= own;
    }
    // A record larger than the limit is a turn that cannot be kept at all.
    if arrival.bytes > limit {
        drops::add(&landing, 1, arrival.bytes, now)?;
        private_mkdir(arrival.tree)?;
        atomic_write(&marker, "", false)?;
        return Ok(false);
    }
    delete_oldest(&landing, &closed, total, limit - arrival.bytes, now)?;
    Ok(true)
}

/// When `turn` closed: its state's `closed_at`, else its file's time.
fn closed_at(turn: &Closed) -> String {
    match State::read(&Queue::part(&turn.directory, turn.n, "state")) {
        Ok(state) => state.closed_at,
        Err(_) => turn
            .modified
            .map_or_else(String::new, |at| micros_z(stamp(at))),
    }
}

/// Deletes closed turns, the oldest closed first, until `total` is at most
/// `room`, by when each closed, a tie by number. A conversation whose
/// landing lock another process holds is skipped whole. Each turn is
/// counted before its files go: the turn, its state, its reason. What
/// cannot go stays.
fn delete_oldest(
    landing: &Path,
    conversations: &[Conversation],
    mut total: u64,
    room: u64,
    now: Stamp,
) -> io::Result<()> {
    // Each conversation's turns in the order they closed: by number, or by
    // every turn's `closed_at` where a reason file says a number may not
    // follow it (`usage::Closed`).
    let orders: Vec<Vec<usize>> = conversations
        .iter()
        .map(|conversation| {
            let turns = &conversation.turns;
            let mut order: Vec<usize> = (0..turns.len()).collect();
            if turns.iter().any(|turn| turn.reason) {
                let at: Vec<String> = turns.iter().map(closed_at).collect();
                order.sort_by(|a, b| at[*a].cmp(&at[*b]).then(a.cmp(b)));
            }
            order
        })
        .collect();
    let head = |index: usize, next: usize| {
        let turn = &conversations[index].turns[orders[index][next]];
        (closed_at(turn), index, next)
    };
    // Each conversation's oldest remaining turn: when it closed, which
    // conversation, which place in its order.
    let mut heads: Vec<(String, usize, usize)> = (0..conversations.len())
        .map(|index| head(index, 0))
        .collect();
    while total > room {
        let Some(position) = (0..heads.len()).min_by(|a, b| heads[*a].cmp(&heads[*b])) else {
            break;
        };
        let (_, index, next) = heads[position];
        let conversation = &conversations[index];
        let Some(lock) = Queue::new(&conversation.tree).try_lock(&conversation.id)? else {
            heads.swap_remove(position);
            continue;
        };
        let turn = &conversation.turns[orders[index][next]];
        // Under the lock: a turn landed or set aside meanwhile is not there.
        if let Some(metadata) = stat(&Queue::part(&turn.directory, turn.n, "jsonl"))? {
            drops::add(landing, 1, metadata.len(), now)?;
            for part in ["jsonl", "state", "reason"] {
                remove_file(&Queue::part(&turn.directory, turn.n, part))?;
            }
        }
        total = total.saturating_sub(turn.bytes);
        tidy(lock)?;
        match orders[index].get(next + 1) {
            Some(_) => heads[position] = head(index, next + 1),
            None => {
                heads.swap_remove(position);
            }
        }
    }
    Ok(())
}
