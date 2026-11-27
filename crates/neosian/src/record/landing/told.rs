//! What the session-start notices have told (DESIGN §40.54 item 17.8; the
//! T1e2b slice and the owner rulings of the T1e2 plan):
//! `<home>/spool/landing/notices.ack`, beside the destination records but
//! not named `.json`, so the listing of records never takes it for one.
//! It holds identities and counters only, no content and no secret: each
//! block told (its record's key, the host it was told with, its reason and
//! since), each day of failures told (key and `failing_since`), the names
//! of the held and of the unserved turns told (`snapshot::Turn`), and the
//! drop counts told with the time of the home's first deletion. A file
//! that cannot be read counts as nothing told, so its notices are shown
//! again rather than lost.
//!
//! A claim holds `notices.lock`, taken without waiting: a session start
//! that finds it taken tells nothing and leaves its notices to a later
//! start. The file is written whole by a rename once the notices were
//! written out (`startup.rs`).

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::snapshot::Snapshot;
use crate::store::fileio::{atomic_write, private, private_mkdir};

const FILE: &str = "notices.ack";
const LOCK: &str = "notices.lock";

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Told {
    pub blocks: Vec<Block>,
    pub failing: Vec<Failing>,
    pub held: BTreeSet<String>,
    pub unserved: BTreeSet<String>,
    pub dropped: Option<Dropped>,
}

/// A block told: the next start that finds no block for `key` tells its
/// end, with `host`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Block {
    pub key: String,
    pub host: String,
    pub reason: String,
    pub since: String,
}

/// A day of failures told.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Failing {
    pub key: String,
    pub since: String,
}

/// The drop counts told, and `drops.json`'s `first` they were told with:
/// another `first` means the counts started again.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Dropped {
    pub first: Option<String>,
    pub turns: u64,
    pub bytes: u64,
}

/// What a notice tells, to be kept once it was written out.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Claim {
    Block(Block),
    Unblock(String),
    Failing(Failing),
    Held(Vec<String>),
    Unserved(Vec<String>),
    Dropped(Dropped),
}

fn path(landing: &Path) -> PathBuf {
    landing.join(FILE)
}

/// The file's bytes, `None` when there is none, and what it holds.
pub fn read(landing: &Path) -> (Option<Vec<u8>>, Told) {
    let bytes = fs::read(path(landing)).ok();
    let told = bytes
        .as_deref()
        .and_then(|bytes| serde_json::from_slice(bytes).ok())
        .unwrap_or_default();
    (bytes, told)
}

/// `notices.lock` taken without waiting, or `None` when another process
/// holds it or it cannot be opened.
pub fn try_lock(landing: &Path) -> Option<File> {
    private_mkdir(landing).ok()?;
    let file = private()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(landing.join(LOCK))
        .ok()?;
    file.try_lock().ok()?;
    Some(file)
}

pub fn write(landing: &Path, told: &Told) -> std::io::Result<()> {
    let mut text = serde_json::to_string(told).expect("identities serialize");
    text.push('\n');
    atomic_write(&path(landing), &text, false)
}

impl Told {
    /// What is told once `claims` were written out, with `snapshot`, read
    /// under the lock, saying what is still there: a held or unserved name
    /// no longer held or pending, and a day of failures that ended, are
    /// forgotten. A block is forgotten only when its end is told.
    pub fn after(&self, claims: &[&Claim], snapshot: &Snapshot) -> Self {
        let mut told = self.clone();
        for claim in claims {
            match claim {
                Claim::Block(block) => {
                    told.blocks.retain(|old| old.key != block.key);
                    told.blocks.push(block.clone());
                }
                Claim::Unblock(key) => told.blocks.retain(|old| old.key != *key),
                Claim::Failing(failing) => {
                    told.failing.retain(|old| old.key != failing.key);
                    told.failing.push(failing.clone());
                }
                Claim::Held(names) => told.held.extend(names.iter().cloned()),
                Claim::Unserved(names) => told.unserved.extend(names.iter().cloned()),
                Claim::Dropped(dropped) => told.dropped = Some(dropped.clone()),
            }
        }
        let (held, pending) = (snapshot.names(true), snapshot.names(false));
        told.held.retain(|name| held.contains(name.as_str()));
        told.unserved.retain(|name| pending.contains(name.as_str()));
        told.failing.retain(|failing| {
            snapshot.places.iter().any(|place| {
                place.key == failing.key
                    && place.record.as_ref().and_then(|r| r.failing_since.as_ref())
                        == Some(&failing.since)
            })
        });
        told.blocks.sort_by(|a, b| a.key.cmp(&b.key));
        told.failing.sort_by(|a, b| a.key.cmp(&b.key));
        told
    }
}
