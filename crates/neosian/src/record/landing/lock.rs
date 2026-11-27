//! A conversation's landing lock, `pending/<id>/.lock` (DESIGN §40.54
//! items 17.2 and 17.3): only ever taken with `try_lock`, so a process
//! that finds it taken leaves the conversation queued and never waits.
//! Once every pending turn of the conversation has gone, the holder
//! removes the counter, the lock file and the empty directory; a handle to a lock file
//! unlinked that way is not the lock.

use std::fs::{self, File, TryLockError};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use super::files::COUNTER;
use crate::store::fileio::{private, private_mkdir, remove_file};

const LOCK: &str = ".lock";

/// Holding it is holding the lock.
pub struct Lock {
    _file: File,
    directory: PathBuf,
}

/// The lock of the conversation whose pending turns are in `directory`,
/// or `None` while another process holds it.
pub fn try_lock(directory: &Path) -> io::Result<Option<Lock>> {
    private_mkdir(directory)?;
    let path = directory.join(LOCK);
    let mut file = private().read(true).write(true).create(true).open(&path)?;
    if file.metadata()?.len() == 0 {
        file.write_all(b"\0")?;
    }
    match file.try_lock() {
        Ok(()) => {}
        Err(TryLockError::WouldBlock) => return Ok(None),
        Err(TryLockError::Error(error)) => return Err(error),
    }
    if !same_file(&file, &path)? {
        return Ok(None);
    }
    Ok(Some(Lock {
        _file: file,
        directory: directory.to_path_buf(),
    }))
}

/// Removes the directory, its counter and its lock file when nothing else
/// is in it, while `lock` is held: the holder has done with every turn it
/// took, so no number the counter keeps is held (`queue.rs`). Windows
/// keeps the directory and the lock, since an open lock file cannot be
/// told from a new one there.
pub fn tidy(lock: Lock) -> io::Result<()> {
    if cfg!(windows) {
        return Ok(());
    }
    let mut counted = false;
    for entry in fs::read_dir(&lock.directory)? {
        let name = entry?.file_name();
        counted |= name == COUNTER;
        if name != LOCK && name != COUNTER {
            return Ok(());
        }
    }
    if counted {
        remove_file(&lock.directory.join(COUNTER))?;
    }
    remove_file(&lock.directory.join(LOCK))?;
    // Not empty: a close started meanwhile, and its turn keeps the directory.
    match fs::remove_dir(&lock.directory) {
        Err(error)
            if !matches!(
                error.kind(),
                io::ErrorKind::NotFound | io::ErrorKind::DirectoryNotEmpty
            ) =>
        {
            Err(error)
        }
        _ => Ok(()),
    }
}

#[cfg(unix)]
fn same_file(file: &File, path: &Path) -> io::Result<bool> {
    use std::os::unix::fs::MetadataExt;
    let held = file.metadata()?;
    match fs::metadata(path) {
        Ok(now) => Ok(held.dev() == now.dev() && held.ino() == now.ino()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

#[cfg(not(unix))]
fn same_file(_: &File, _: &Path) -> io::Result<bool> {
    Ok(true)
}
