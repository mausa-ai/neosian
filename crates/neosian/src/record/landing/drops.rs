//! What the keep limit deleted (DESIGN §40.54 items 17.7 and 17.8; section
//! E.1 of the T1e1 plan): `<home>/spool/landing/drops.json` beside the
//! destination records, one per home, for every spool tree of it. It holds
//! counts and times, never content: `turns` deleted, `bytes` deleted (the
//! turns' files, an open span's file, and the records dropped with it), and
//! `first` and `last`, when the first and the latest deletion happened, in
//! UTC. A count only grows; what reads it to tell the person keeps its own
//! mark of what was told. Every change is a read, a change and an atomic
//! write under `landing/.lock`, written before the files it counts are
//! deleted, so a crash between the two can count a turn that stays but
//! never deletes one that was not counted. A record that cannot be read
//! starts again from zero.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::destination::lock;
use super::draft::micros_z;
use crate::store::fileio::atomic_write;
use crate::store::time::Stamp;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Drops {
    pub turns: u64,
    pub bytes: u64,
    pub first: Option<String>,
    pub last: Option<String>,
}

fn path(landing: &Path) -> PathBuf {
    landing.join("drops.json")
}

/// The home's drop counts under `landing`, or `None` when nothing was
/// deleted or the record cannot be read.
pub fn read(landing: &Path) -> Option<Drops> {
    serde_json::from_slice(&fs::read(path(landing)).ok()?).ok()
}

/// Counts `turns` more turns and `bytes` more bytes deleted at `now`.
pub fn add(landing: &Path, turns: u64, bytes: u64, now: Stamp) -> io::Result<()> {
    let _held = lock(landing)?;
    let mut drops = read(landing).unwrap_or_default();
    let at = micros_z(now);
    drops.turns += turns;
    drops.bytes += bytes;
    if drops.first.is_none() {
        drops.first = Some(at.clone());
    }
    drops.last = Some(at);
    let mut text = serde_json::to_string(&drops).expect("counts serialize");
    text.push('\n');
    atomic_write(&path(landing), &text, false)
}
