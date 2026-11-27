//! The copy a snapshot is made of (the L1b slice of the L1 plan): the
//! durable store data of the person's home, taken without changing it.
//!
//! The copy takes every top-level directory that spells a scope segment,
//! and `conversations`, `.records` and `.transactions`, file by file.
//! Nothing else of the home is read, so the login directory with its
//! credential, the configuration, profiles and the recording queue never
//! enter it. A symbolic link or a special file is refused by name, never
//! followed. Files are opened for reading only and written private.
//!
//! The copy is coherent. When the home has its store lock file, the lock is
//! held while the copy is taken, through the existing file opened for
//! reading (the protocol of `store/lock.rs`), so the file is neither created
//! nor written. When the home has none, no FileStore process ever opened
//! it; the copy is taken unlocked and kept only if the lock file is still
//! absent afterwards, since any writer creates it first. Otherwise the copy
//! is taken again under the lock. The lock is held across nothing but the
//! local copy, never across a network wait.
//!
//! Recovery and validation run on the copy alone: it is opened as a
//! FileStore, whose first locked read replays a pending memory redo plan,
//! and listing its conversations settles each conversation directory's
//! commit, erasure, migration, fold and seal journals.

use std::fs::{self, File, TryLockError};
use std::io;
use std::path::Path;
use std::thread;
use std::time::Duration;

use super::snapshot::Fault;
use crate::conversation::Error;
use crate::store::FileStore;
use crate::store::fileio::{private, private_builder, remove_tree};

/// The FileStore lock at a store's root (`FileStore::lock`).
const SOURCE_LOCK: &str = ".store.lock";
/// A home's durable store data besides its scope directories.
const KEPT: [&str; 3] = ["conversations", ".records", ".transactions"];
const RETRY: Duration = Duration::from_millis(10);

/// The home's store lock, held through the existing file opened for
/// reading: never created, never written. None when the home has no lock
/// file.
fn hold_existing(home: &Path) -> Result<Option<File>, Fault> {
    let path = home.join(SOURCE_LOCK);
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
        Ok(meta) if !meta.is_file() => {
            return Err(Fault::Unsupported {
                path: SOURCE_LOCK.to_string(),
                what: "a lock that is not a plain file",
            });
        }
        Ok(_) => {}
    }
    let file = File::open(&path)?;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(Some(file)),
            Err(TryLockError::WouldBlock) => thread::sleep(RETRY),
            Err(TryLockError::Error(error)) => return Err(error.into()),
        }
    }
}

/// The copy, under the home's lock when it has one; an unlocked copy is
/// kept only when no lock file appeared meanwhile, else taken once more,
/// under the lock that appeared.
pub(super) fn coherent_copy(home: &Path, copy: &Path) -> Result<(), Fault> {
    for _ in 0..2 {
        let held = hold_existing(home)?;
        let locked = held.is_some();
        let copied = copy_store(home, copy);
        drop(held);
        copied?;
        if locked || fs::symlink_metadata(home.join(SOURCE_LOCK)).is_err() {
            return Ok(());
        }
        remove_tree(copy)?;
    }
    Err(Fault::Busy)
}

fn copy_store(home: &Path, copy: &Path) -> Result<(), Fault> {
    private_builder().create(copy)?;
    let mut names = Vec::new();
    for entry in fs::read_dir(home)? {
        let entry = entry?;
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        if !(KEPT.contains(&name.as_str()) || name.contains("%3A")) {
            continue;
        }
        let kind = entry.file_type()?;
        if kind.is_symlink() {
            return Err(Fault::Unsupported {
                path: name,
                what: "a symbolic link",
            });
        }
        // A plain file under a store name is foreign; the store never reads it.
        if kind.is_dir() {
            names.push(name);
        }
    }
    names.sort();
    for name in names {
        copy_tree(&home.join(&name), &copy.join(&name), &name)?;
    }
    Ok(())
}

fn copy_tree(from: &Path, to: &Path, shown: &str) -> Result<(), Fault> {
    private_builder().create(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let name = entry.file_name();
        let shown = format!("{shown}/{}", name.to_string_lossy());
        let kind = entry.file_type()?;
        if kind.is_dir() {
            copy_tree(&entry.path(), &to.join(&name), &shown)?;
        } else if kind.is_file() {
            let mut source = File::open(entry.path())?;
            let mut target = private()
                .write(true)
                .create_new(true)
                .open(to.join(&name))?;
            io::copy(&mut source, &mut target)?;
        } else {
            let what = if kind.is_symlink() {
                "a symbolic link"
            } else {
                "a special file"
            };
            return Err(Fault::Unsupported { path: shown, what });
        }
    }
    Ok(())
}

/// Recovery and validation on the copy: the memory redo plan is replayed
/// by the first locked read, and listing the conversations settles each
/// conversation directory's journals.
pub(super) fn recover(copy: &Path) -> Result<(), Fault> {
    let store = FileStore::open(copy)?;
    store.scopes().map_err(Error::from)?;
    store.conversations().map_err(Error::from)?;
    Ok(())
}
