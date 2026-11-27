//! The login import's private snapshot (the L1b slice of the L1 plan,
//! section F's ruling on resume and cleanup).
//!
//! A snapshot is a coherent copy of the durable store data of the person's
//! home, taken without changing the home (`copy.rs`). Recovery and
//! validation run on the copy, never on the home. Then every file of the
//! copy is listed in a manifest with its length and SHA-256 (`manifest.rs`),
//! and the snapshot is sealed by renaming its directory.
//! The snapshot's id is the SHA-256 of that list; opening the snapshot later
//! checks every file against it, so a resumed import reads the same bytes.
//!
//! ```text
//! <home>/login-import/          0700, removed by disposal
//!     snapshot/                 the sealed snapshot
//!         store/                a FileStore root: the copy, recovered
//!         manifest.json         0600: taken_at and the file list
//!     <uuid>.partial/           a take in progress, or one that died
//!     <uuid>.disposing/         a disposal in progress, or one that died
//! ```
//!
//! A snapshot lives until it is disposed of explicitly. It is past its
//! lifetime once the host's import session would have ended (24 hours, the
//! consent's clock); the caller then disposes of it and says so.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::copy::{coherent_copy, recover};
use super::manifest::{self, Listed};
use crate::conversation::Error;
use crate::store::FileStore;
use crate::store::fileio::{private, private_builder, private_mkdir, remove_tree};
use crate::store::time::Stamp;

/// Where a home keeps its login import's snapshot.
pub(crate) const DIRECTORY: &str = "login-import";
const SEALED: &str = "snapshot";
const STORE: &str = "store";
const MANIFEST: &str = "manifest.json";
/// How long a snapshot serves an import: the host session's 24 hours.
pub(crate) const LIFETIME_SECONDS: i64 = 86_400;

/// Why a snapshot was not taken or opened.
#[derive(Debug)]
pub(crate) enum Fault {
    /// A sealed snapshot is already there: resume it or dispose of it.
    Exists { id: String },
    /// The sealed snapshot no longer matches its manifest.
    Changed { path: String },
    /// The home holds something the copy does not take.
    Unsupported { path: String, what: &'static str },
    /// The home had no store lock, and a writer started during each copy.
    Busy,
    /// The copy could not be read or written.
    Store(Error),
}

impl From<io::Error> for Fault {
    fn from(error: io::Error) -> Self {
        Self::Store(error.into())
    }
}

impl From<Error> for Fault {
    fn from(error: Error) -> Self {
        Self::Store(error)
    }
}

impl Fault {
    pub(crate) fn message(&self) -> String {
        match self {
            Self::Exists { id } => format!(
                "a login import snapshot is already taken ({id}); resume that import or dispose \
                 of the snapshot first"
            ),
            Self::Changed { path } => format!(
                "the login import snapshot changed since it was taken ({path} differs); dispose \
                 of it and start the import again"
            ),
            Self::Unsupported { path, what } => format!(
                "the home holds {what} at {path}; the import copies no link or special file, so \
                 move it out of the home and try again"
            ),
            Self::Busy => "the home kept changing while it was copied; stop the agents writing \
                 to it and try again"
                .to_string(),
            Self::Store(error) => error.text(),
        }
    }
}

/// A sealed snapshot.
#[derive(Debug)]
pub(crate) struct Snapshot {
    /// `<home>/login-import/snapshot`.
    pub path: PathBuf,
    /// The SHA-256 of the manifest's file list.
    pub id: String,
    pub taken_at: Stamp,
    pub files: u64,
    pub bytes: u64,
}

impl Snapshot {
    /// The copy's root.
    pub(crate) fn root(&self) -> PathBuf {
        self.path.join(STORE)
    }

    /// The copy as a FileStore.
    pub(crate) fn store(&self) -> io::Result<FileStore> {
        FileStore::open(&self.root())
    }

    pub(crate) fn expired(&self, now: Stamp) -> bool {
        now.instant() - self.taken_at.instant() > LIFETIME_SECONDS * 1_000_000
    }
}

/// Takes a snapshot of `home`. The home is not changed; the snapshot is
/// written under `<home>/login-import`.
pub(crate) fn take(home: &Path) -> Result<Snapshot, Fault> {
    if !home.is_dir() {
        let shown = home.display();
        return Err(Error::Value(format!("there is no home at {shown} to import")).into());
    }
    let area = home.join(DIRECTORY);
    private_mkdir(&area)?;
    if let Some(sealed) = sealed(&area)? {
        return Err(Fault::Exists { id: sealed.0 });
    }
    let partial = area.join(format!("{}.partial", uuid::Uuid::new_v4()));
    private_builder().create(&partial)?;
    let made = (|| {
        let taken_at = Stamp::now();
        let copy = partial.join(STORE);
        coherent_copy(home, &copy)?;
        recover(&copy)?;
        let listed = manifest::list(&copy)?;
        let text = manifest::render(taken_at, &listed);
        let mut file = private()
            .write(true)
            .create_new(true)
            .open(partial.join(MANIFEST))?;
        io::Write::write_all(&mut file, text.as_bytes())?;
        file.sync_all()?;
        Ok::<_, Fault>((taken_at, listed))
    })();
    let (taken_at, listed) = match made {
        Ok(made) => made,
        Err(fault) => {
            // Nothing of a failed take stays: its copy, and the area when empty.
            let _ = remove_tree(&partial);
            let _ = fs::remove_dir(&area);
            return Err(fault);
        }
    };
    let path = area.join(SEALED);
    if let Err(error) = fs::rename(&partial, &path) {
        let _ = remove_tree(&partial);
        return Err(match sealed(&area)? {
            Some((id, _)) => Fault::Exists { id },
            None => error.into(),
        });
    }
    Ok(snapshot(path, taken_at, &listed))
}

fn snapshot(path: PathBuf, taken_at: Stamp, listed: &Listed) -> Snapshot {
    Snapshot {
        path,
        id: manifest::id(listed),
        taken_at,
        files: listed.len() as u64,
        bytes: listed.iter().map(|file| file.1).sum(),
    }
}

/// The sealed snapshot's id and time, from its manifest alone.
fn sealed(area: &Path) -> Result<Option<(String, Stamp)>, Fault> {
    let path = area.join(SEALED).join(MANIFEST);
    match fs::read_to_string(&path) {
        Ok(text) => {
            let (taken_at, listed) = manifest::parse(&text).ok_or(Fault::Changed {
                path: MANIFEST.to_string(),
            })?;
            Ok(Some((manifest::id(&listed), taken_at)))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            if area.join(SEALED).exists() {
                return Err(Fault::Changed {
                    path: MANIFEST.to_string(),
                });
            }
            Ok(None)
        }
        Err(error) => Err(error.into()),
    }
}

/// Opens the sealed snapshot of `home`, every file checked against its
/// manifest; None when there is none.
pub(crate) fn open(home: &Path) -> Result<Option<Snapshot>, Fault> {
    let area = home.join(DIRECTORY);
    let Some((_, taken_at)) = sealed(&area)? else {
        return Ok(None);
    };
    let path = area.join(SEALED);
    let text = fs::read_to_string(path.join(MANIFEST))?;
    let (_, recorded) = manifest::parse(&text).ok_or(Fault::Changed {
        path: MANIFEST.to_string(),
    })?;
    let found = manifest::list(&path.join(STORE))?;
    if let Some(path) = manifest::first_difference(&recorded, &found) {
        return Err(Fault::Changed { path });
    }
    Ok(Some(snapshot(path, taken_at, &found)))
}

/// Removes the snapshot and everything else under `<home>/login-import`,
/// then that directory; true when there was anything to remove. The sealed
/// snapshot is renamed before it is removed, so a disposal that dies never
/// leaves a half snapshot that reads as sealed.
pub(crate) fn dispose(home: &Path) -> Result<bool, Fault> {
    let area = home.join(DIRECTORY);
    match fs::symlink_metadata(&area) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
        Ok(meta) if !meta.is_dir() => {
            return Err(Fault::Unsupported {
                path: DIRECTORY.to_string(),
                what: "something other than a directory",
            });
        }
        Ok(_) => {}
    }
    if area.join(SEALED).exists() {
        let doomed = area.join(format!("{}.disposing", uuid::Uuid::new_v4()));
        fs::rename(area.join(SEALED), doomed)?;
    }
    for entry in fs::read_dir(&area)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            remove_tree(&entry.path())?;
        } else {
            fs::remove_file(entry.path())?;
        }
    }
    fs::remove_dir(&area)?;
    Ok(true)
}

/// The manifest's JSON form, for the diagnostic verb.
pub(crate) fn describe(snapshot: &Snapshot, now: Stamp) -> serde_json::Value {
    json!({
        "id": snapshot.id,
        "path": snapshot.path.to_string_lossy(),
        "taken_at": snapshot.taken_at.z(),
        "files": snapshot.files,
        "bytes": snapshot.bytes,
        "expired": snapshot.expired(now),
    })
}
