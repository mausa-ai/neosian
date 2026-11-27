//! The two landing slots of a home, `<home>/spool/landing/slot-1.lock`
//! and `slot-2.lock` (DESIGN §40.54 items 17.3 and 23; ruling D4 of the
//! T1e1 plan): a lander holds one for its whole run and writes its pid
//! into it, so at most two land at once for every spool tree of the home.
//! A hook probes both with `try_lock` and releases them before it spawns;
//! a lander that finds both taken exits at once. Nothing waits on a slot,
//! and the system releases it with its holder, so a lander that dies
//! frees its slot.

use std::fs::{File, TryLockError};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use crate::store::fileio::{private, private_mkdir};

const SLOTS: [&str; 2] = ["slot-1.lock", "slot-2.lock"];

/// Where a home's slots are.
pub fn directory(home: &Path) -> PathBuf {
    home.join("spool").join("landing")
}

/// A slot this process holds while it lives.
pub struct Slot {
    _file: File,
    pub number: usize,
}

fn open(home: &Path, name: &str) -> io::Result<File> {
    let directory = directory(home);
    private_mkdir(&directory)?;
    private()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(name))
}

fn taken_by_us(file: &File) -> io::Result<bool> {
    match file.try_lock() {
        Ok(()) => Ok(true),
        Err(TryLockError::WouldBlock) => Ok(false),
        Err(TryLockError::Error(error)) => Err(error),
    }
}

/// Whether a slot is free now. Each is probed with `try_lock`, and a
/// probe that takes one releases it at once, before any spawn.
pub fn free(home: &Path) -> io::Result<bool> {
    for name in SLOTS {
        if taken_by_us(&open(home, name)?)? {
            return Ok(true);
        }
    }
    Ok(false)
}

/// A slot for this lander, its pid written into it, or `None` when both
/// are taken.
pub fn take(home: &Path) -> io::Result<Option<Slot>> {
    for (index, name) in SLOTS.iter().enumerate() {
        let mut file = open(home, name)?;
        if taken_by_us(&file)? {
            file.set_len(0)?;
            file.write_all(format!("{}\n", std::process::id()).as_bytes())?;
            return Ok(Some(Slot {
                _file: file,
                number: index + 1,
            }));
        }
    }
    Ok(None)
}
