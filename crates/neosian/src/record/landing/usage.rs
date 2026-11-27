//! What a home's spool trees keep (DESIGN §40.54 items 17.2 and 17.7;
//! ruling D12 of the T1e1 plan): one scan, at every record a hook appends
//! while `[record] keep_bytes` is set, of the bytes of every open span
//! (`<id>.jsonl`) and every closed turn (`pending/<id>/<n>.jsonl`,
//! `held/<id>/<n>.jsonl`) in the home's `spool/`, `profile-spool/` and the
//! hook's own tree: a directory listing per conversation and a stat per
//! file, no file read and no total kept between hooks.

use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use super::files::{Part, entries};

const QUEUES: [&str; 2] = ["pending", "held"];

/// The home's own spool trees and the hook's, once each.
pub fn trees(home: &Path, tree: &Path) -> Vec<PathBuf> {
    let mut trees = vec![home.join("spool"), home.join("profile-spool")];
    if !trees.iter().any(|own| own == tree) {
        trees.push(tree.to_path_buf());
    }
    trees
}

/// A conversation's closed turns in one tree, pending and held together,
/// by number: the order they closed, since a closed turn takes the next
/// number of its conversation.
pub struct Conversation {
    pub tree: PathBuf,
    pub id: String,
    pub turns: Vec<Closed>,
}

/// A closed turn the scan found: where, its size and its file's time.
pub struct Closed {
    pub directory: PathBuf,
    pub n: u64,
    pub bytes: u64,
    pub modified: Option<SystemTime>,
}

/// A file's metadata, or `None` once it is gone.
pub fn stat(path: &Path) -> io::Result<Option<fs::Metadata>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => Ok(Some(metadata)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

/// The entries of `directory`, none when it is missing.
fn listing(directory: &Path) -> io::Result<Vec<fs::DirEntry>> {
    match fs::read_dir(directory) {
        Ok(listed) => listed.collect(),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(error) => Err(error),
    }
}

/// The bytes of every open span and closed turn under `trees`, and the
/// closed turns by conversation. A file gone between the listing and its
/// stat counts nothing: it landed or closed meanwhile.
pub fn scan(trees: &[PathBuf]) -> io::Result<(u64, Vec<Conversation>)> {
    let mut total = 0;
    let mut found: BTreeMap<(PathBuf, String), Vec<Closed>> = BTreeMap::new();
    for tree in trees {
        for entry in listing(tree)? {
            if entry
                .file_name()
                .to_str()
                .is_some_and(|name| name.ends_with(".jsonl"))
            {
                total += stat(&entry.path())?.map_or(0, |metadata| metadata.len());
            }
        }
        for queue in QUEUES {
            for conversation in listing(&tree.join(queue))? {
                if !conversation.file_type()?.is_dir() {
                    continue;
                }
                let directory = conversation.path();
                let id = conversation.file_name().to_string_lossy().into_owned();
                for (n, parts) in entries(&directory)? {
                    let Some((_, file)) = parts.iter().find(|(part, _)| *part == Part::Span) else {
                        continue;
                    };
                    let Some(metadata) = stat(file)? else {
                        continue;
                    };
                    total += metadata.len();
                    let turns = found.entry((tree.clone(), id.clone())).or_default();
                    turns.push(Closed {
                        directory: directory.clone(),
                        n,
                        bytes: metadata.len(),
                        modified: metadata.modified().ok(),
                    });
                }
            }
        }
    }
    let conversations = found.into_iter().map(|((tree, id), mut turns)| {
        turns.sort_by_key(|turn| turn.n);
        Conversation { tree, id, turns }
    });
    Ok((total, conversations.collect()))
}
