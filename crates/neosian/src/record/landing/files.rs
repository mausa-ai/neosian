//! The queue's file operations (DESIGN §40.54 item 17.2): the names of a
//! turn's parts, syncs, a private write of bytes by a rename, and the
//! removal of what a cut leaves behind. Leftovers are removed once they
//! are a minute old, since a younger one may be a close or a hold in
//! progress in another process.

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::store::fileio::{private, private_mkdir, remove_file};
use crate::store::time::Stamp;

/// How old a leftover must be before a listing removes it.
const ORPHAN_AGE: Duration = Duration::from_secs(60);
/// The prefix of `atomic_write`'s temporary files and of `write_bytes`'.
const TEMPORARY: &str = ".neosian-tmp-";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Part {
    Span,
    State,
    Reason,
}

/// `<n>.jsonl`, `<n>.state` or `<n>.reason`, `n` without leading zeros.
fn parse_name(name: &str) -> Option<(u64, Part)> {
    let (stem, extension) = name.split_once('.')?;
    let part = match extension {
        "jsonl" => Part::Span,
        "state" => Part::State,
        "reason" => Part::Reason,
        _ => return None,
    };
    Some((number(stem)?, part))
}

fn number(stem: &str) -> Option<u64> {
    let canonical = !stem.is_empty()
        && stem.bytes().all(|b| b.is_ascii_digit())
        && (stem == "0" || !stem.starts_with('0'));
    stem.parse().ok().filter(|_| canonical)
}

/// The numbered parts in `directory`, by number; none when it is missing.
pub fn entries(directory: &Path) -> io::Result<BTreeMap<u64, Vec<(Part, PathBuf)>>> {
    let mut found: BTreeMap<u64, Vec<(Part, PathBuf)>> = BTreeMap::new();
    let listing = match fs::read_dir(directory) {
        Ok(listing) => listing,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(found),
        Err(error) => return Err(error),
    };
    for entry in listing {
        let entry = entry?;
        if let Some((n, part)) = entry.file_name().to_str().and_then(parse_name) {
            found.entry(n).or_default().push((part, entry.path()));
        }
    }
    Ok(found)
}

/// Whether `directory` holds a turn.
pub fn holds_turn(directory: &Path) -> io::Result<bool> {
    Ok(entries(directory)?
        .values()
        .any(|parts| parts.iter().any(|(part, _)| *part == Part::Span)))
}

/// A file's bytes on disk. Windows flushes only a handle opened for writing.
pub fn sync_file(path: &Path) -> io::Result<()> {
    OpenOptions::new().write(true).open(path)?.sync_all()
}

#[cfg(unix)]
pub fn sync_directory(path: &Path) -> io::Result<()> {
    fs::File::open(path)?.sync_all()
}

#[cfg(not(unix))]
pub fn sync_directory(_: &Path) -> io::Result<()> {
    Ok(())
}

/// `bytes` written whole to `file` through a private temporary file beside
/// it and a rename, never re-encoded.
pub fn write_bytes(file: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = file.parent().unwrap_or(Path::new("."));
    private_mkdir(parent)?;
    let mut attempt = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| since.subsec_nanos());
    let (temp, mut handle) = loop {
        let temp = parent.join(format!("{TEMPORARY}{}-{attempt:08x}", std::process::id()));
        match private().write(true).create_new(true).open(&temp) {
            Ok(handle) => break (temp, handle),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => attempt += 1,
            Err(error) => return Err(error),
        }
    };
    let written = handle
        .write_all(bytes)
        .and_then(|()| fs::rename(&temp, file));
    if written.is_err() {
        let _ = fs::remove_file(&temp);
    }
    written
}

/// When the span was opened: its file's creation time where the file
/// system keeps one, else now.
pub fn opened_at(span: &Path) -> Stamp {
    fs::metadata(span)
        .and_then(|metadata| metadata.created())
        .ok()
        .and_then(|created| created.duration_since(UNIX_EPOCH).ok())
        .map_or_else(Stamp::now, |since| {
            Stamp::from_micros(since.as_micros() as i64)
        })
}

fn old(path: &Path) -> bool {
    fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|modified| SystemTime::now().duration_since(modified).ok())
        .is_some_and(|age| age > ORPHAN_AGE)
}

/// Removes from a queue directory, once a minute old, a state or reason
/// without its turn, a hold's `.<n>.state.copy` and a temporary file.
pub fn sweep(directory: &Path) -> io::Result<()> {
    let listing = match fs::read_dir(directory) {
        Ok(listing) => listing,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let mut leftovers = Vec::new();
    for entry in listing {
        let name = entry?.file_name();
        let Some(name) = name.to_str() else { continue };
        let copy = name
            .strip_prefix('.')
            .and_then(|rest| rest.strip_suffix(".state.copy"))
            .and_then(number);
        if name.starts_with(TEMPORARY) || copy.is_some() {
            leftovers.push(directory.join(name));
        }
    }
    for (_, parts) in entries(directory)? {
        if !parts.iter().any(|(part, _)| *part == Part::Span) {
            leftovers.extend(parts.into_iter().map(|(_, path)| path));
        }
    }
    for path in leftovers {
        if old(&path) {
            remove_file(&path)?;
        }
    }
    Ok(())
}
