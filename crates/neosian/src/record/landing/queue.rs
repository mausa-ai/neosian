//! The queue of closed turns under one spool tree (DESIGN §40.54 items
//! 17.1, 17.2 and 17.6, #447): `pending/<id>/<n>.jsonl` with its
//! `<n>.state`, `held/<id>/<n>.jsonl` with its state and `<n>.reason`.
//! `n` is unique per conversation across both and ascends in the order
//! turns closed, and it is never taken again while a lander may hold the
//! turn that had it: a pause that removes pending turns while another
//! process holds the conversation's lock first raises the conversation's
//! counter, `pending/<id>/.next`, past them and leaves it there (ruling G1
//! of the T1e1 plan); the lock's holder removes it once the directory
//! holds nothing else. A turn's `.jsonl` is its commit point: its state (and in
//! `held/` its reason) is written first, so a turn without a state is
//! never produced; what a cut leaves is removed by a later listing
//! (`files::sweep`). Every step is a rename, so a crashed process leaves a
//! whole file or none; `persist` syncs a closed turn that stays queued or
//! is held. A span's bytes are never re-encoded: its opening line is put
//! before them. The conversation's landing lock is `lock.rs`.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use super::draft::{is_opening, opening_line};
use super::files::{
    COUNTER, Part, entries, highest, holds_turn, opened_at, sweep, sync_directory, sync_file,
    write_bytes,
};
use super::lock::{Lock, tidy, try_lock};
use super::state::{Reason, State};
use crate::store::fileio::{atomic_write, remove_file};

const PENDING: &str = "pending";
const HELD: &str = "held";

/// The opening line `bytes` begin with, if they begin with one.
pub fn opening_of(bytes: &[u8]) -> Option<&str> {
    let first = bytes.split(|byte| *byte == b'\n').next().unwrap_or(&[]);
    std::str::from_utf8(first)
        .ok()
        .filter(|line| is_opening(line))
}

/// Whether any conversation of the spool tree at `root` has a closed turn
/// that has not landed: one waiting in `pending/`, or one set aside in
/// `held/` (ruling E17).
pub fn waiting(root: &Path) -> bool {
    [PENDING, HELD].iter().any(|queue| {
        fs::read_dir(root.join(queue)).is_ok_and(|conversations| {
            conversations
                .flatten()
                .any(|conversation| holds_turn(&conversation.path()).unwrap_or(false))
        })
    })
}

pub struct Queue {
    root: PathBuf,
}

impl Queue {
    /// The queue of the spool tree at `root` (`spool/`, `profile-spool/`
    /// or a `--spool DIR`).
    pub fn new(root: &Path) -> Self {
        Self {
            root: root.to_path_buf(),
        }
    }

    pub fn pending_dir(&self, id: &str) -> PathBuf {
        self.root.join(PENDING).join(id)
    }

    pub fn held_dir(&self, id: &str) -> PathBuf {
        self.root.join(HELD).join(id)
    }

    /// The path of `n`'s part in `directory`: `jsonl`, `state` or `reason`.
    pub fn part(directory: &Path, n: u64, extension: &str) -> PathBuf {
        directory.join(format!("{n}.{extension}"))
    }

    /// One more than every number the conversation's pending and held
    /// turns (and any state left by a cut) use, and at least its counter.
    /// The counter is read only when the listing finds it.
    pub fn next_n(&self, id: &str) -> io::Result<u64> {
        let (pending, counted) = highest(&self.pending_dir(id))?;
        let (held, _) = highest(&self.held_dir(id))?;
        let next = pending.max(held).map_or(1, |n| n + 1);
        match counted {
            true => Ok(next.max(self.counter(id)?)),
            false => Ok(next),
        }
    }

    /// The conversation's counter; 0 when it is missing or damaged, so the
    /// listing alone decides.
    fn counter(&self, id: &str) -> io::Result<u64> {
        match fs::read_to_string(self.pending_dir(id).join(COUNTER)) {
            Ok(text) => Ok(text.trim().parse().unwrap_or(0)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(0),
            Err(error) => Err(error),
        }
    }

    /// Closes the open span at `span`, whose bytes are `bytes`, into the
    /// queue: pending, or held with `reason`. The span is given its
    /// opening line first when it has none (in place, its bytes after the
    /// line unchanged); then the state `state(opening line, length)` is
    /// written; `between` runs (a cut, for tests); then the rename.
    /// Returns `n`.
    pub fn close(
        &self,
        span: &Path,
        bytes: &[u8],
        id: &str,
        reason: Option<&Reason>,
        state: impl FnOnce(&str, u64) -> State,
        between: impl FnOnce() -> io::Result<()>,
    ) -> io::Result<u64> {
        let (line, length) = match opening_of(bytes) {
            Some(line) => (line.to_string(), bytes.len()),
            None => {
                let nonce = uuid::Uuid::new_v4().to_string();
                let opening = opening_line(opened_at(span), &nonce);
                let mut whole = Vec::with_capacity(opening.len() + 1 + bytes.len());
                whole.extend_from_slice(opening.as_bytes());
                whole.push(b'\n');
                whole.extend_from_slice(bytes);
                write_bytes(span, &whole)?;
                (opening, whole.len())
            }
        };
        let n = self.next_n(id)?;
        let directory = match reason {
            Some(_) => self.held_dir(id),
            None => self.pending_dir(id),
        };
        state(&line, length as u64).write(&Self::part(&directory, n, "state"))?;
        if let Some(reason) = reason {
            atomic_write(&Self::part(&directory, n, "reason"), &reason.text(), false)?;
        }
        between()?;
        fs::rename(span, Self::part(&directory, n, "jsonl"))?;
        Ok(n)
    }

    /// Makes closed turn `n` in `directory` durable: its files, the
    /// directory, the `pending/` or `held/` directory that holds its
    /// entry, and the spool tree whose entry the rename moved. Called for
    /// a turn that stays queued or is held; a turn the hook lands at once
    /// is gone before a power loss could need it. A turn already removed
    /// (stored, its document failed), whose emptied directory the lock's
    /// tidy may have removed too, needs nothing and is not an error.
    pub fn persist(&self, directory: &Path, n: u64) -> io::Result<()> {
        match fs::symlink_metadata(Self::part(directory, n, "jsonl")) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(error),
            Ok(_) => {}
        }
        for extension in ["jsonl", "state", "reason"] {
            match sync_file(&Self::part(directory, n, extension)) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                other => other?,
            }
        }
        sync_directory(directory)?;
        sync_directory(directory.parent().unwrap_or(&self.root))?;
        sync_directory(&self.root)
    }

    /// The conversation's pending turns in `n` order, after what cuts left
    /// in its `pending/` and `held/` directories is swept.
    pub fn pending(&self, id: &str) -> io::Result<Vec<u64>> {
        let directory = self.pending_dir(id);
        sweep(&directory)?;
        sweep(&self.held_dir(id))?;
        Ok(entries(&directory)?
            .into_iter()
            .filter(|(_, parts)| parts.iter().any(|(part, _)| *part == Part::Span))
            .map(|(n, _)| n)
            .collect())
    }

    /// Whether the conversation has a pending turn.
    pub fn has_pending(&self, id: &str) -> io::Result<bool> {
        holds_turn(&self.pending_dir(id))
    }

    /// Moves pending turn `n` to `held/` under the same number with
    /// `reason`: the reason and a copy of the state first, then the turn,
    /// then the pending state goes.
    pub fn hold(&self, id: &str, n: u64, reason: &Reason) -> io::Result<()> {
        self.hold_with(id, n, reason, || Ok(()))
    }

    /// `hold` with `between` run before the turn moves (a cut, for tests).
    pub fn hold_with(
        &self,
        id: &str,
        n: u64,
        reason: &Reason,
        between: impl FnOnce() -> io::Result<()>,
    ) -> io::Result<()> {
        let (from, to) = (self.pending_dir(id), self.held_dir(id));
        atomic_write(&Self::part(&to, n, "reason"), &reason.text(), true)?;
        let state = Self::part(&from, n, "state");
        if state.is_file() {
            let copy = to.join(format!(".{n}.state.copy"));
            fs::copy(&state, &copy)?;
            sync_file(&copy)?;
            fs::rename(&copy, Self::part(&to, n, "state"))?;
        }
        between()?;
        fs::rename(Self::part(&from, n, "jsonl"), Self::part(&to, n, "jsonl"))?;
        sync_directory(&to)?;
        sync_directory(to.parent().unwrap_or(&self.root))?;
        remove_file(&state)?;
        sync_directory(&from)
    }

    /// Removes pending turn `n`: the turn, then its state.
    pub fn remove(&self, id: &str, n: u64) -> io::Result<()> {
        let directory = self.pending_dir(id);
        remove_file(&Self::part(&directory, n, "jsonl"))?;
        remove_file(&Self::part(&directory, n, "state"))
    }

    /// Discards every pending turn of the conversation (`record pause`,
    /// owner O2, taken provisionally); held turns stay. While another
    /// process holds the conversation's lock, a lander may be sending one
    /// of them, so the counter is raised past them first and left in
    /// place: no turn closed later takes their numbers (ruling G1).
    pub fn discard_pending(&self, id: &str) -> io::Result<()> {
        let numbers = self.pending(id)?;
        let Some(last) = numbers.last().copied() else {
            return Ok(());
        };
        let free = self.try_lock(id)?;
        if free.is_none() {
            let next = self.counter(id)?.max(last + 1);
            let counter = self.pending_dir(id).join(COUNTER);
            atomic_write(&counter, &format!("{next}\n"), true)?;
        }
        for n in numbers {
            self.remove(id, n)?;
        }
        Ok(())
    }

    /// Holds `unreadable`, under the conversation's lock, every pending
    /// turn whose state cannot be read or names no destination: no
    /// destination's landing reaches such a turn (ruling G7). The number
    /// held, or `None` while another process holds the lock.
    pub fn set_aside_unreadable(&self, id: &str) -> io::Result<Option<usize>> {
        let Some(lock) = self.try_lock(id)? else {
            return Ok(None);
        };
        let mut held = 0;
        for n in self.pending(id)? {
            let why = match State::read(&Self::part(&self.pending_dir(id), n, "state")) {
                Ok(state) if state.destination.is_some() => continue,
                Ok(_) => "the state names no destination".to_string(),
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::NotFound | io::ErrorKind::InvalidData
                    ) =>
                {
                    error.to_string()
                }
                Err(error) => return Err(error),
            };
            self.hold(id, n, &Reason::unreadable(why))?;
            held += 1;
        }
        tidy(lock)?;
        Ok(Some(held))
    }

    /// Whether `record pause` has marked the conversation paused: its
    /// `<id>.paused` beside the open spans, published before the pause
    /// discards the conversation's pending turns.
    pub fn paused(&self, id: &str) -> io::Result<bool> {
        self.marker(id).try_exists()
    }

    /// Where `record pause` puts the conversation's marker.
    pub fn marker(&self, id: &str) -> PathBuf {
        self.root.join(format!("{id}.paused"))
    }

    /// The conversation's landing lock (`lock.rs`), or `None` while another
    /// process holds it.
    pub fn try_lock(&self, id: &str) -> io::Result<Option<Lock>> {
        try_lock(&self.pending_dir(id))
    }
}
