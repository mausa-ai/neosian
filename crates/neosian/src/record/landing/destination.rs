//! A destination's record (DESIGN §40.54 items 17.4, 17.5 and 17.8;
//! section E.1 and E.3 of the T1e1 plan): `<home>/spool/landing/<key>.json`
//! beside the slots, one per destination whose answers made it wait, fail
//! or block, gone again once nothing of that is left. `key` is the first 32
//! hex digits of the SHA-256 of the descriptor's JSON (`state.rs`), so a
//! queued turn's state names its record. The record holds no content and
//! no secret: the host as a person knows it (a URL's host and port, never
//! its userinfo), times in UTC, and the status, code and message of the
//! answer. Every change is a read, a change and an atomic write under
//! `landing/.lock`; a hook only reads. A record that cannot be read counts
//! as none, so a damaged file never stops landing for good.
//!
//! - `wait_until`: no lander lands to the destination before then (a 429
//!   with a longer or no `Retry-After`, a 5xx, no answer, a fault).
//! - `block`: `reason` (`storage`, `forbidden`, `credential`), `status`,
//!   `code`, `message`, `since` (when that block began) and `next_attempt`
//!   (six hours after the last refusal, when one part may be tried).
//! - `failing_since`: when the current run of faults began, kept across
//!   runs until a turn lands; `cause`: what the last wait or block said.

use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use super::super::fingerprint::digest;
use super::draft::micros_z;
use super::rules::Stop;
use super::state::Destination;
use crate::store::fileio::{atomic_write, private, private_mkdir, remove_file};
use crate::store::time::Stamp;

/// How long a block lasts before one part is tried (17.5, O10).
pub const SIX_HOURS: i64 = 6 * 3600;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub host: String,
    pub wait_until: Option<String>,
    pub block: Option<Blocked>,
    pub failing_since: Option<String>,
    pub cause: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Blocked {
    pub reason: String,
    pub status: Option<u16>,
    pub code: Option<String>,
    pub message: String,
    pub since: String,
    pub next_attempt: String,
}

/// Whether a lander may land to a destination now.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Gate {
    Open,
    /// It waits, or it is blocked and its six hours have not passed.
    Closed,
    /// It is blocked and its six hours have passed: one part may be tried.
    Due,
}

/// A stamp of `time` on the wall clock.
pub fn stamp(time: SystemTime) -> Stamp {
    let since = time.duration_since(UNIX_EPOCH).unwrap_or_default();
    Stamp::from_micros(i64::try_from(since.as_micros()).unwrap_or(i64::MAX))
}

/// Whether `at` is reached at `now`; a time that cannot be read is.
fn reached(at: Option<&str>, now: Stamp) -> bool {
    at.is_none_or(|at| Stamp::parse(at).map_or(true, |at| at.instant() <= now.instant()))
}

impl Record {
    pub fn gate(&self, now: Stamp) -> Gate {
        if !reached(self.wait_until.as_deref(), now) {
            return Gate::Closed;
        }
        match &self.block {
            None => Gate::Open,
            Some(block) if reached(Some(&block.next_attempt), now) => Gate::Due,
            Some(_) => Gate::Closed,
        }
    }

    /// Nothing is left to say: the record goes.
    fn empty(&self) -> bool {
        self.wait_until.is_none() && self.block.is_none() && self.failing_since.is_none()
    }

    /// A wait of `seconds` from `now`, at most six hours (a `Retry-After`
    /// is a server's word, and a stamp cannot hold every number).
    fn wait(&mut self, seconds: u64, failing: bool, now: Stamp) {
        let seconds = i64::try_from(seconds).unwrap_or(SIX_HOURS).min(SIX_HOURS);
        self.wait_until = Some(micros_z(now.after(seconds)));
        if failing && self.failing_since.is_none() {
            self.failing_since = Some(micros_z(now));
        }
    }

    /// The record after `stop`, an answer about the whole destination, at
    /// `now`. A wait never ends a block; a block ends a wait. A block of the
    /// same reason keeps its `since`.
    pub fn noted(mut self, host: &str, stop: &Stop, now: Stamp) -> Self {
        host.clone_into(&mut self.host);
        self.cause = Some(stop.message().to_string());
        match stop {
            Stop::Wait {
                seconds, failing, ..
            } => self.wait(*seconds, *failing, now),
            Stop::Retry { seconds, .. } => self.wait(*seconds, false, now),
            Stop::Block {
                block,
                message,
                status,
                code,
            } => {
                let since = match &self.block {
                    Some(old) if old.reason == block.name() => old.since.clone(),
                    _ => micros_z(now),
                };
                self.wait_until = None;
                self.block = Some(Blocked {
                    reason: block.name().to_string(),
                    status: *status,
                    code: code.clone(),
                    message: message.clone(),
                    since,
                    next_attempt: micros_z(now.after(SIX_HOURS)),
                });
            }
            Stop::Conversation(_) => {}
        }
        self
    }
}

/// The record's name for `destination`.
pub fn key(destination: &Destination) -> String {
    let json = serde_json::to_vec(destination).expect("a descriptor serializes");
    digest(&json)[..32].to_string()
}

fn path(landing: &Path, key: &str) -> PathBuf {
    landing.join(format!("{key}.json"))
}

/// The record named `key` under `landing`, or `None` when there is none
/// or it cannot be read.
pub fn read(landing: &Path, key: &str) -> Option<Record> {
    let text = fs::read(path(landing, key)).ok()?;
    serde_json::from_slice(&text).ok()
}

/// Whether a lander may land to `destination` now, as a hook asks it.
pub fn gate(landing: &Path, destination: &Destination, now: Stamp) -> Gate {
    read(landing, &key(destination)).map_or(Gate::Open, |record| record.gate(now))
}

/// Holding it is holding `landing/.lock`.
pub(super) struct Locked {
    _file: File,
}

pub(super) fn lock(landing: &Path) -> io::Result<Locked> {
    private_mkdir(landing)?;
    let file = private()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(landing.join(".lock"))?;
    file.lock()?;
    Ok(Locked { _file: file })
}

/// Reads, changes and writes the record named `key` under the lock; what
/// `change` answers is written, or the file removed when nothing is left.
/// Answers what `change` returned beside.
pub fn update<T>(
    landing: &Path,
    key: &str,
    change: impl FnOnce(Option<Record>) -> (Option<Record>, T),
) -> io::Result<T> {
    let _held = lock(landing)?;
    let (record, answer) = change(read(landing, key));
    let file = path(landing, key);
    match record.filter(|record| !record.empty()) {
        Some(record) => {
            let mut text = serde_json::to_string(&record).expect("a record serializes");
            text.push('\n');
            atomic_write(&file, &text, false)?;
        }
        None => remove_file(&file)?,
    }
    Ok(answer)
}

/// Records `stop`, an answer about the destination, at `now`.
pub fn note(landing: &Path, key: &str, host: &str, stop: &Stop, now: Stamp) -> io::Result<()> {
    update(landing, key, |record| {
        (Some(record.unwrap_or_default().noted(host, stop, now)), ())
    })
}

/// Changes the record named `key`, if there is one, under the lock.
fn edit(landing: &Path, key: &str, change: impl FnOnce(&mut Record)) -> io::Result<()> {
    update(landing, key, |record| {
        let record = record.map(|mut record| {
            change(&mut record);
            record
        });
        (record, ())
    })
}

/// A turn landed: the faults and any wait are over.
pub fn landed(landing: &Path, key: &str) -> io::Result<()> {
    edit(landing, key, |record| {
        (record.wait_until, record.failing_since, record.cause) = (None, None, None);
    })
}

/// The attempt's part was taken: the block ends.
pub fn lift(landing: &Path, key: &str) -> io::Result<()> {
    edit(landing, key, |record| record.block = None)
}

/// Claims the one attempt at a blocked destination that is due: its next
/// attempt moves six hours on, so no other lander tries it meanwhile.
/// Answers the block as it was, or `None` when it is not due (any more).
pub fn claim(landing: &Path, key: &str, now: Stamp) -> io::Result<Option<Blocked>> {
    update(landing, key, |record| match record {
        Some(mut record) if record.gate(now) == Gate::Due => {
            let was = record.block.clone();
            if let Some(block) = &mut record.block {
                block.next_attempt = micros_z(now.after(SIX_HOURS));
            }
            (Some(record), was)
        }
        other => (other, None),
    })
}

/// Gives a claimed attempt that sent nothing back: its next attempt is
/// what it was.
pub fn unclaim(landing: &Path, key: &str, was: &Blocked) -> io::Result<()> {
    edit(landing, key, |record| {
        if let Some(block) = &mut record.block
            && block.reason == was.reason
        {
            block.next_attempt.clone_from(&was.next_attempt);
        }
    })
}

/// Ends the blocks `which` selects, and with `waits` every wait too, of
/// every destination recorded under `landing`: what each record was, for
/// those that changed.
pub fn unblock(
    landing: &Path,
    which: impl Fn(&Blocked) -> bool,
    waits: bool,
) -> io::Result<Vec<Record>> {
    let mut ended = Vec::new();
    for key in super::keys::keys(landing)? {
        let was = update(landing, &key, |record| {
            let Some(mut after) = record.clone() else {
                return (None, None);
            };
            let blocked = after.block.take_if(|block| which(block)).is_some();
            let waiting = waits && after.wait_until.take().is_some();
            match blocked || waiting {
                true => (Some(after), record),
                false => (record, None),
            }
        })?;
        ended.extend(was);
    }
    Ok(ended)
}
