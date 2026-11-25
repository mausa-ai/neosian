//! Private file I/O (`shared/fileio.py`, ledger #126): everything the store
//! leaves on a user's disk is theirs alone, files 0600 and directories
//! 0700, set at creation and never retrofitted.

use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
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
    atomic_write_with(file, sync, |handle| handle.write_all(text.as_bytes()))
}

pub(super) fn atomic_write_with<E: From<io::Error>>(
    file: &Path,
    sync: bool,
    write: impl FnOnce(&mut File) -> Result<(), E>,
) -> Result<(), E> {
    let parent = file.parent().unwrap_or(Path::new("."));
    private_mkdir(parent)?;
    let mut attempt = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| since.subsec_nanos());
    let (temp, mut handle) = loop {
        let temp = parent.join(format!(".neosian-tmp-{}-{attempt:08x}", process::id()));
        match private()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&temp)
        {
            Ok(handle) => break (temp, handle),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => attempt += 1,
            Err(error) => return Err(error.into()),
        }
    };
    let written = (|| {
        write(&mut handle)?;
        if sync {
            handle.sync_all()?;
        }
        fs::rename(&temp, file)?;
        Ok(())
    })();
    if written.is_err() {
        let _ = fs::remove_file(&temp);
    }
    written
}

/// Append in one write; a new file is born private, an existing one
/// keeps its mode.
pub fn append_line(file: &Path, text: &str, sync: bool) -> io::Result<()> {
    append(file, text, sync, false)
}

/// Numbering accepts a complete final turn row without a line ending.
/// Separate that row from its successor while holding the same store lock.
/// An interruption after the separator leaves the old log readable.
pub fn append_turn_line(file: &Path, text: &str, sync: bool) -> io::Result<()> {
    append(file, text, sync, true)
}

fn append(file: &Path, text: &str, sync: bool, check_ending: bool) -> io::Result<()> {
    if let Some(parent) = file.parent() {
        private_mkdir(parent)?;
    }
    let mut handle = private()
        .append(true)
        .read(check_ending)
        .create(true)
        .open(file)?;
    if check_ending {
        let len = handle.metadata()?.len();
        if needs_separator(&mut handle, len)? {
            handle.write_all(b"\n")?;
        }
    }
    handle.write_all(text.as_bytes())?;
    if sync { handle.sync_all() } else { Ok(()) }
}

/// Check the last byte of a retained JSONL prefix, leaving the cursor at its end.
pub(super) fn needs_separator(file: &mut File, prefix: u64) -> io::Result<bool> {
    if prefix == 0 {
        file.rewind()?;
        return Ok(false);
    }
    file.seek(SeekFrom::Start(prefix - 1))?;
    let mut last = [0];
    file.read_exact(&mut last)?;
    Ok(last[0] != b'\n')
}

/// Cleanup and transaction replay may encounter an already-removed entry.
fn allow_absent(result: io::Result<()>) -> io::Result<()> {
    match result {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        result => result,
    }
}

pub fn remove_file(path: &Path) -> io::Result<()> {
    allow_absent(fs::remove_file(path))
}

pub fn remove_tree(path: &Path) -> io::Result<()> {
    allow_absent(fs::remove_dir_all(path))
}
