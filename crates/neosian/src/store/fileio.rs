//! Private file I/O (`shared/fileio.py`, ledger #126): everything the store
//! leaves on a user's disk is theirs alone, files 0600 and directories
//! 0700, set at creation and never retrofitted.

use std::fs::{self, DirBuilder, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::process;
use std::time::{SystemTime, UNIX_EPOCH};

/// Resolve the deepest existing component, including dangling symlinks.
/// Callers hold the store lock; this is not a sandbox against hostile OS writers.
pub fn within(root: &Path, candidate: &Path) -> io::Result<bool> {
    let mut existing = candidate;
    loop {
        match fs::symlink_metadata(existing) {
            Ok(_) => break,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                let Some(parent) = existing.parent() else {
                    return Ok(false);
                };
                existing = parent;
            }
            Err(error) => return Err(error),
        }
    }
    match existing.canonicalize() {
        Ok(resolved) => Ok(resolved
            .join(candidate.strip_prefix(existing).unwrap_or(candidate))
            .starts_with(root)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

/// `mkdir -p` with every newly created level private.
pub fn private_mkdir(directory: &Path) -> io::Result<()> {
    let mut missing = Vec::new();
    let mut current = directory;
    while !current.exists() {
        missing.push(current);
        match current.parent() {
            Some(parent) => current = parent,
            None => break,
        }
    }
    let builder = private_builder();
    for level in missing.into_iter().rev() {
        match builder.create(level) {
            Err(error) if error.kind() != io::ErrorKind::AlreadyExists => return Err(error),
            _ => {}
        }
    }
    Ok(())
}

/// A directory builder whose levels are born 0700. The mode is set on
/// Unix alone, so the two platforms build it apart: a shared `let mut`
/// would be unused on Windows.
#[cfg(unix)]
pub(crate) fn private_builder() -> DirBuilder {
    use std::os::unix::fs::DirBuilderExt;
    let mut builder = DirBuilder::new();
    builder.mode(0o700);
    builder
}

#[cfg(not(unix))]
pub(crate) fn private_builder() -> DirBuilder {
    DirBuilder::new()
}

/// Open options under which a new file is born private (0600 on Unix).
#[cfg(unix)]
pub fn private() -> OpenOptions {
    use std::os::unix::fs::OpenOptionsExt;
    let mut options = OpenOptions::new();
    options.mode(0o600);
    options
}

#[cfg(not(unix))]
pub fn private() -> OpenOptions {
    OpenOptions::new()
}

/// Write through a temp file beside the destination and a rename: never
/// partial, and born private. `sync` flushes the bytes to disk first, so
/// the rename never outruns what it points at.
pub fn atomic_write(file: &Path, text: &str, sync: bool) -> io::Result<()> {
    let parent = file.parent().unwrap_or(Path::new("."));
    private_mkdir(parent)?;
    let mut attempt = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| since.subsec_nanos());
    let (temp, mut handle) = loop {
        let temp = parent.join(format!(".neosian-tmp-{}-{attempt:08x}", process::id()));
        match private().write(true).create_new(true).open(&temp) {
            Ok(handle) => break (temp, handle),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => attempt += 1,
            Err(error) => return Err(error),
        }
    };
    let written = handle
        .write_all(text.as_bytes())
        .and_then(|()| if sync { handle.sync_all() } else { Ok(()) })
        .and_then(|()| fs::rename(&temp, file));
    if written.is_err() {
        let _ = fs::remove_file(&temp);
    }
    written
}

/// Append in one write; a new file is born private, an existing one
/// keeps its mode.
pub fn append_line(file: &Path, text: &str, sync: bool) -> io::Result<()> {
    if let Some(parent) = file.parent() {
        private_mkdir(parent)?;
    }
    let mut handle = private().append(true).create(true).open(file)?;
    handle.write_all(text.as_bytes())?;
    if sync { handle.sync_all() } else { Ok(()) }
}
