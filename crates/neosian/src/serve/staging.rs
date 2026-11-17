//! Where `neosian serve` spools answers (DESIGN §40.46, #419): one root per
//! process, `.neosian-serve-<uuid>` in the temporary directory, private,
//! holding a `lock` file the process keeps locked while it runs and one
//! private directory per answer on disk. A root whose lock nobody holds
//! belongs to a server that has ended (killed, or crashed); the next server
//! to start removes it. Nothing else in the temporary directory is touched.

use std::fs::{self, File, Metadata, TryLockError};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, SystemTime};

use crate::store::fileio::{private, private_builder};

const PREFIX: &str = ".neosian-serve-";
const LOCK: &str = "lock";
/// A root without its lock file is one another server is still making,
/// unless it is older than this.
const UNFINISHED: Duration = Duration::from_secs(3600);

struct Root {
    directory: PathBuf,
    _lock: File,
}

static ROOT: Mutex<Option<Root>> = Mutex::new(None);

/// This process's root, made on first use, and made again if it vanished.
pub fn root() -> io::Result<PathBuf> {
    let mut held = ROOT.lock().unwrap_or_else(PoisonError::into_inner);
    if let Some(root) = held.as_ref()
        && root.directory.is_dir()
    {
        return Ok(root.directory.clone());
    }
    let root = make(&std::env::temp_dir())?;
    let directory = root.directory.clone();
    *held = Some(root);
    Ok(directory)
}

fn make(parent: &Path) -> io::Result<Root> {
    let directory = parent.join(format!("{PREFIX}{}", uuid::Uuid::new_v4()));
    private_builder().create(&directory)?;
    let locked = (|| {
        let mut lock = private()
            .read(true)
            .write(true)
            .create_new(true)
            .open(directory.join(LOCK))?;
        lock.write_all(b"\0")?;
        match lock.try_lock() {
            Ok(()) => Ok(lock),
            Err(TryLockError::WouldBlock) => Err(io::Error::other("the new root's lock is held")),
            Err(TryLockError::Error(error)) => Err(error),
        }
    })();
    match locked {
        Ok(lock) => Ok(Root {
            directory,
            _lock: lock,
        }),
        Err(error) => {
            let _ = fs::remove_dir_all(&directory);
            Err(error)
        }
    }
}

/// At startup: make this process's root, remove the roots of servers that
/// have ended, and try the root by writing a file in an answer directory.
/// Answers how many ended roots were removed; the error says why answers
/// over 1 MiB will be held in memory instead.
pub fn prepare() -> io::Result<usize> {
    let ours = root()?;
    let removed = sweep(&ours);
    let probe = ours.join(format!("probe-{}", uuid::Uuid::new_v4()));
    private_builder().create(&probe)?;
    let written = private()
        .write(true)
        .create_new(true)
        .open(probe.join("answer.json"))
        .and_then(|mut file| file.write_all(&[b' '; 4096]));
    let removed_probe = fs::remove_dir_all(&probe);
    written?;
    removed_probe?;
    Ok(removed)
}

/// Remove this process's root when the server stops.
pub fn close() {
    let mut held = ROOT.lock().unwrap_or_else(PoisonError::into_inner);
    if let Some(root) = held.take() {
        let directory = root.directory.clone();
        drop(root);
        let _ = fs::remove_dir_all(directory);
    }
}

/// Remove ended servers' roots beside `ours`: a directory (never a link)
/// named `.neosian-serve-<uuid>`, owned by this user, whose lock file nobody
/// holds, or that has had no lock file for an hour.
fn sweep(ours: &Path) -> usize {
    let (Some(parent), Ok(owner)) = (ours.parent(), fs::symlink_metadata(ours)) else {
        return 0;
    };
    let Ok(entries) = fs::read_dir(parent) else {
        return 0;
    };
    let mut removed = 0;
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(id) = name.to_str().and_then(|name| name.strip_prefix(PREFIX)) else {
            continue;
        };
        let path = entry.path();
        if uuid::Uuid::parse_str(id).is_err() || path == ours {
            continue;
        }
        let Ok(found) = fs::symlink_metadata(&path) else {
            continue;
        };
        if found.file_type().is_dir()
            && same_owner(&found, &owner)
            && ended(&path, &found)
            && fs::remove_dir_all(&path).is_ok()
        {
            removed += 1;
        }
    }
    removed
}

fn ended(root: &Path, found: &Metadata) -> bool {
    match private().read(true).write(true).open(root.join(LOCK)) {
        // Taking the lock proves its owner is gone; it is let go at once.
        Ok(lock) => matches!(lock.try_lock(), Ok(())),
        Err(error) if error.kind() == io::ErrorKind::NotFound => found
            .modified()
            .ok()
            .and_then(|modified| SystemTime::now().duration_since(modified).ok())
            .is_some_and(|age| age > UNFINISHED),
        Err(_) => false,
    }
}

#[cfg(unix)]
fn same_owner(found: &Metadata, ours: &Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    found.uid() == ours.uid()
}

#[cfg(not(unix))]
fn same_owner(_found: &Metadata, _ours: &Metadata) -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_ended_servers_roots_are_swept() {
        let parent = std::env::temp_dir().join(format!("neosian-sweep-{}", uuid::Uuid::new_v4()));
        private_builder().create(&parent).unwrap();
        let ours = make(&parent).unwrap();
        let live = make(&parent).unwrap();
        let ended = make(&parent).unwrap();
        fs::write(ended.directory.join("answer.json"), "x").unwrap();
        let ended_path = ended.directory.clone();
        drop(ended);
        // Lookalikes that are not ours to remove.
        let foreign = [
            parent.join(".neosian-serve-not-a-uuid"),
            parent.join(".neosian-stage-0b8f4a4c-7a52-4c0e-9d0f-6f6f0d5d2c11"),
        ];
        for path in &foreign {
            private_builder().create(path).unwrap();
        }
        let unfinished = parent.join(format!("{PREFIX}{}", uuid::Uuid::new_v4()));
        private_builder().create(&unfinished).unwrap();
        assert_eq!(sweep(&ours.directory), 1);
        assert!(!ended_path.exists());
        assert!(live.directory.join(LOCK).exists());
        assert!(unfinished.exists(), "a root still being made is kept");
        for path in &foreign {
            assert!(path.exists());
        }
        drop((ours, live));
        fs::remove_dir_all(parent).unwrap();
    }
}
