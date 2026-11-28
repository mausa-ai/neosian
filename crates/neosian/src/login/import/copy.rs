//! The copy a snapshot is made of (the L1b slice of the L1 plan): the
//! durable store data of the person's home, taken without changing it.
//!
//! The copy admits what the FileStore itself reads (L1e, N3): `conversations`,
//! `.records` and `.transactions` whole, and each scope directory, a
//! directory whose name spells a scope segment and whose path from the root
//! parses as a scope. Of a scope directory it takes `documents/`,
//! `versions/`, `redactions.jsonl` and the scope directories below it, and
//! nothing else; a directory that is not a scope (`backup%3Aold%3Acopy`) is
//! not copied. The login directory with its credential, the configuration,
//! profiles and the recording queue never enter it. A symbolic link or a
//! special file among what is admitted is refused by name, never followed.
//! Files are opened for reading only and written private.
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
use crate::store::grammar::parse_scope;
use crate::store::layout::{DOCUMENTS, REDACTIONS, VERSIONS};

/// The FileStore lock at a store's root (`FileStore::lock`).
const SOURCE_LOCK: &str = ".store.lock";
/// A home's durable store data besides its scope directories.
const KEPT: [&str; 3] = ["conversations", ".records", ".transactions"];
const RETRY: Duration = Duration::from_millis(10);

/// Whether `name`, below the scope `parent` (empty at the root), is a
/// scope directory: the FileStore's own rule (`store::walk_scopes`).
fn scope_named(parent: &str, name: &str) -> Option<String> {
    let segment = name.contains("%3A").then(|| name.replace("%3A", ":"))?;
    let scope = match parent {
        "" => segment,
        _ => format!("{parent}/{segment}"),
    };
    parse_scope(&scope).is_ok().then_some(scope)
}

/// Whether `home` has a directory the copy would take, read from its
/// listing alone (L1c): a home without one has nothing to offer.
pub(crate) fn holds_store(home: &Path) -> bool {
    fs::read_dir(home).is_ok_and(|entries| {
        entries.flatten().any(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            (KEPT.contains(&name.as_str()) || scope_named("", &name).is_some())
                && entry.file_type().is_ok_and(|kind| kind.is_dir())
        })
    })
}

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
    for (name, scope) in admitted(home, "", "")? {
        let (from, to) = (home.join(&name), copy.join(&name));
        match scope {
            None => copy_tree(&from, &to, &name)?,
            Some(scope) => copy_scope(&from, &to, &name, &scope)?,
        }
    }
    Ok(())
}

/// The directories of `dir` the store reads, sorted, each with its scope
/// (None for a directory kept whole). `shown` is `dir` from the root.
fn admitted(dir: &Path, parent: &str, shown: &str) -> Result<Vec<(String, Option<String>)>, Fault> {
    let mut found = Vec::new();
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        let scope = scope_named(parent, &name);
        if scope.is_none() && !(parent.is_empty() && KEPT.contains(&name.as_str())) {
            continue;
        }
        let kind = entry.file_type()?;
        if kind.is_symlink() {
            let path = format!("{shown}{name}");
            return Err(Fault::Unsupported {
                path,
                what: "a symbolic link",
            });
        }
        // A plain file under a store name is foreign; the store never reads it.
        if kind.is_dir() {
            found.push((name, scope));
        }
    }
    found.sort();
    Ok(found)
}

/// One scope directory: its documents, versions and trail, then the scope
/// directories below it.
fn copy_scope(from: &Path, to: &Path, shown: &str, scope: &str) -> Result<(), Fault> {
    private_builder().create(to)?;
    for part in [DOCUMENTS, VERSIONS, REDACTIONS] {
        let source = from.join(part);
        let Ok(meta) = fs::symlink_metadata(&source) else {
            continue;
        };
        let path = format!("{shown}/{part}");
        if meta.is_dir() && part != REDACTIONS {
            copy_tree(&source, &to.join(part), &path)?;
        } else if meta.is_file() && part == REDACTIONS {
            copy_file(&source, &to.join(part))?;
        } else if meta.file_type().is_symlink() {
            return Err(Fault::Unsupported {
                path,
                what: "a symbolic link",
            });
        }
    }
    for (name, below) in admitted(from, scope, &format!("{shown}/"))? {
        let below = below.expect("below a scope only scopes are admitted");
        copy_scope(
            &from.join(&name),
            &to.join(&name),
            &format!("{shown}/{name}"),
            &below,
        )?;
    }
    Ok(())
}

fn copy_file(from: &Path, to: &Path) -> Result<(), Fault> {
    let mut source = File::open(from)?;
    let mut target = private().write(true).create_new(true).open(to)?;
    io::copy(&mut source, &mut target)?;
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
            copy_file(&entry.path(), &to.join(&name))?;
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
