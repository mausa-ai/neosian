//! The advisory lock cooperating local processes share (`shared/
//! filelock.py`, DESIGN §39.3): a private file beside what it guards, one
//! NUL byte in it, an exclusive non-blocking lock retried every 10 ms. It
//! is never unlinked, because a waiter must keep competing on the same
//! inode; the lock ends when the returned file closes, a process's death
//! included.

use std::fs::{File, TryLockError};
use std::io::{self, Write};
use std::path::Path;
use std::thread;
use std::time::Duration;

use super::fileio::{private, private_mkdir};

const RETRY: Duration = Duration::from_millis(10);

/// Wait for the lock at `path`; holding the file is holding the lock.
pub fn acquire(path: &Path) -> io::Result<File> {
    if let Some(parent) = path.parent() {
        private_mkdir(parent)?;
    }
    let mut file = private().read(true).write(true).create(true).open(path)?;
    if file.metadata()?.len() == 0 {
        file.write_all(b"\0")?;
    }
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(file),
            Err(TryLockError::WouldBlock) => thread::sleep(RETRY),
            Err(TryLockError::Error(error)) => return Err(error),
        }
    }
}
