//! Where the file substrate keeps things (`memory/file_layout.py`, DESIGN
//! §8):
//!
//! ```text
//! root/<scope segments, percent-encoded>/
//!     documents/<path>.md · versions/<path>.jsonl · redactions.jsonl
//! ```

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use super::error::{Error, MemoryError};
use super::grammar::validate_document_path;

pub const DOCUMENTS: &str = "documents";
pub const VERSIONS: &str = "versions";
pub const REDACTIONS: &str = "redactions.jsonl";

/// A scope's directory: one component per segment, the `:` percent-encoded
/// (the grammar leaves nothing else to encode), so every component carries
/// a `%3A` and none collides with a reserved name.
pub fn scope_dir(root: &Path, scope: &str) -> PathBuf {
    scope.split('/').fold(root.to_path_buf(), |dir, segment| {
        dir.join(segment.replace(':', "%3A"))
    })
}

pub fn doc_file(root: &Path, scope: &str, path: &str) -> Result<PathBuf, Error> {
    contained(
        root,
        scope_dir(root, scope)
            .join(DOCUMENTS)
            .join(format!("{path}.md")),
        path,
    )
}

pub fn journal_file(root: &Path, scope: &str, path: &str) -> Result<PathBuf, Error> {
    contained(
        root,
        scope_dir(root, scope)
            .join(VERSIONS)
            .join(format!("{path}.jsonl")),
        path,
    )
}

/// Refuse a file a symlink would carry outside the root: the deepest
/// level that exists is resolved, the rest is the grammar's.
pub fn contained(root: &Path, candidate: PathBuf, path: &str) -> Result<PathBuf, Error> {
    if super::fileio::within(root, &candidate)? {
        Ok(candidate)
    } else {
        Err(MemoryError::path_invalid(path, "escapes the store root").into())
    }
}

pub fn trail_file(root: &Path, scope: &str) -> Result<PathBuf, Error> {
    contained(root, scope_dir(root, scope).join(REDACTIONS), REDACTIONS)
}

/// Is a segment of `scope` already on disk under another spelling? A
/// case-folding filesystem finds the directory that the listing does not
/// spell that way, and merging the two would share one version counter
/// (MC-13); a case-sensitive one always answers no.
pub fn case_collision(root: &Path, scope: &str) -> io::Result<bool> {
    let mut parent = root.to_path_buf();
    for segment in scope.split('/') {
        let component = segment.replace(':', "%3A");
        if !parent.join(&component).exists() {
            return Ok(false);
        }
        let mut spelled = false;
        for entry in fs::read_dir(&parent)? {
            spelled |= entry?.file_name() == component.as_str();
        }
        if !spelled {
            return Ok(true);
        }
        parent.push(component);
    }
    Ok(false)
}

/// Every logical path with a `suffix` file under `dir`, sorted. A foreign
/// file whose name is outside the grammar is skipped, never ours to read.
pub fn logical_paths(dir: &Path, suffix: &str) -> io::Result<Vec<String>> {
    fn walk(dir: &Path, prefix: &str, suffix: &str, found: &mut Vec<String>) -> io::Result<()> {
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let Ok(name) = entry.file_name().into_string() else {
                continue;
            };
            if entry.file_type()?.is_dir() {
                walk(&entry.path(), &format!("{prefix}{name}/"), suffix, found)?;
            } else if let Some(stem) = name.strip_suffix(suffix) {
                let logical = format!("{prefix}{stem}");
                if validate_document_path(&logical).is_ok() {
                    found.push(logical);
                }
            }
        }
        Ok(())
    }
    let mut found = Vec::new();
    if dir.is_dir() {
        walk(dir, "", suffix, &mut found)?;
    }
    found.sort();
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_scope_is_one_directory_per_segment() {
        assert_eq!(
            scope_dir(Path::new("/r"), "user:me/proj:x"),
            Path::new("/r").join("user%3Ame").join("proj%3Ax")
        );
    }
}
