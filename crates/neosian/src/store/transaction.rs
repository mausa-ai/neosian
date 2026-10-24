//! One committed redo plan, protected by the root lock, before state or events move.

use std::collections::BTreeSet;
use std::fs;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::{fileio, grammar};

const DIRECTORY: &str = ".transactions";

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
pub enum Operation {
    Put {
        text: String,
    },
    Append {
        offset: u64,
        prefix_sha256: String,
        text: String,
    },
    Remove,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct WriteOp {
    pub path: String,
    #[serde(flatten)]
    pub operation: Operation,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Plan {
    format: u32,
    writes: Vec<WriteOp>,
}

fn bad() -> io::Error {
    io::Error::other("invalid or unsafe memory recovery plan")
}

fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

impl WriteOp {
    pub fn put(root: &Path, path: &Path, text: String) -> io::Result<Self> {
        Self::new(root, path, Operation::Put { text })
    }
    pub fn remove(root: &Path, path: &Path) -> io::Result<Self> {
        Self::new(root, path, Operation::Remove)
    }
    pub fn append(root: &Path, path: &Path, text: String) -> io::Result<Self> {
        let previous = match fs::read(path) {
            Ok(bytes) => bytes,
            Err(e) if e.kind() == io::ErrorKind::NotFound => vec![],
            Err(e) => return Err(e),
        };
        Self::new(
            root,
            path,
            Operation::Append {
                offset: previous.len() as u64,
                prefix_sha256: hash(&previous),
                text,
            },
        )
    }
    fn new(root: &Path, path: &Path, operation: Operation) -> io::Result<Self> {
        let path = path
            .strip_prefix(root)
            .map_err(|_| bad())?
            .to_str()
            .ok_or_else(bad)?
            .replace(std::path::MAIN_SEPARATOR, "/");
        let result = Self { path, operation };
        result.target(root)?;
        Ok(result)
    }
    fn target(&self, root: &Path) -> io::Result<PathBuf> {
        if self.path.is_empty()
            || self.path.contains('\\')
            || Path::new(&self.path)
                .components()
                .any(|c| !matches!(c, Component::Normal(_)))
        {
            return Err(bad());
        }
        let parts: Vec<_> = self.path.split('/').collect();
        let count = parts.iter().take_while(|p| p.contains("%3A")).count();
        let scope = parts[..count]
            .iter()
            .map(|p| p.replace("%3A", ":"))
            .collect::<Vec<_>>()
            .join("/");
        grammar::parse_scope(&scope).map_err(|_| bad())?;
        let tail = &parts[count..];
        let valid = match tail {
            ["redactions.jsonl"] => true,
            [".changes", "head.json"] => true,
            [".exchange", "receipt.json"] => true,
            [".changes", "events", file] => file
                .strip_suffix(".json")
                .is_some_and(|n| n.len() == 20 && n.bytes().all(|b| b.is_ascii_digit())),
            [directory @ ("documents" | "versions"), rest @ ..] if !rest.is_empty() => {
                let path = rest.join("/");
                path.strip_suffix(if *directory == "documents" {
                    ".md"
                } else {
                    ".jsonl"
                })
                .is_some_and(|s| grammar::validate_document_path(s).is_ok())
            }
            _ => false,
        };
        if !valid {
            return Err(bad());
        }
        let target = root.join(&self.path);
        safe(root, &target)?;
        Ok(target)
    }
}

/// Cooperating writers only; never follow a link while replaying a persisted plan.
fn safe(root: &Path, path: &Path) -> io::Result<()> {
    if !fileio::within(root, path)? {
        return Err(bad());
    }
    let mut current = root.to_path_buf();
    for component in path.strip_prefix(root).map_err(|_| bad())?.components() {
        current.push(component);
        match fs::symlink_metadata(&current) {
            Ok(meta) if meta.file_type().is_symlink() => return Err(bad()),
            Err(e) if e.kind() != io::ErrorKind::NotFound => return Err(e),
            _ => {}
        }
    }
    Ok(())
}

fn prefix(path: &Path, offset: u64, expected: &str) -> io::Result<()> {
    let bytes = match fs::File::open(path) {
        Ok(file) => {
            let mut bytes = vec![];
            file.take(offset).read_to_end(&mut bytes)?;
            bytes
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound && offset == 0 => vec![],
        Err(e) => return Err(e),
    };
    if bytes.len() as u64 != offset || hash(&bytes) != expected {
        return Err(bad());
    }
    Ok(())
}

fn validate(root: &Path, plan: &Plan) -> io::Result<()> {
    if plan.format != 1 || plan.writes.is_empty() {
        return Err(bad());
    }
    let mut seen = BTreeSet::new();
    for write in &plan.writes {
        let target = write.target(root)?;
        if !seen.insert(&write.path) {
            return Err(bad());
        }
        if let Operation::Append {
            offset,
            prefix_sha256,
            ..
        } = &write.operation
        {
            prefix(&target, *offset, prefix_sha256)?;
        }
    }
    Ok(())
}

fn clean_temps(directory: &Path) -> io::Result<()> {
    if !directory.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        if entry
            .file_name()
            .to_string_lossy()
            .starts_with(".neosian-tmp-")
            && !entry.file_type()?.is_dir()
        {
            fs::remove_file(entry.path())?;
        }
    }
    Ok(())
}

fn sync_parents(root: &Path, file: &Path) -> io::Result<()> {
    let mut parent = file.parent();
    while let Some(dir) = parent {
        #[cfg(unix)]
        fs::File::open(dir)?.sync_all()?;
        if dir == root {
            break;
        }
        parent = dir.parent();
    }
    Ok(())
}

fn apply(root: &Path, write: &WriteOp) -> io::Result<()> {
    let target = write.target(root)?;
    let parent = target.parent().ok_or_else(bad)?;
    clean_temps(parent)?;
    match &write.operation {
        Operation::Put { text } => fileio::atomic_write(&target, text, true)?,
        Operation::Append { offset, text, .. } => {
            fileio::private_mkdir(parent)?;
            let mut file = fileio::private()
                .write(true)
                .create(true)
                .truncate(false)
                .open(&target)?;
            file.set_len(*offset)?;
            file.seek(SeekFrom::Start(*offset))?;
            file.write_all(text.as_bytes())?;
            file.sync_all()?;
        }
        Operation::Remove => match fs::remove_file(&target) {
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(()),
            result => result?,
        },
    }
    sync_parents(root, &target)
}

pub fn commit(root: &Path, writes: Vec<WriteOp>) -> io::Result<()> {
    commit_with(root, writes, |_| Ok(()))
}

fn commit_with(
    root: &Path,
    writes: Vec<WriteOp>,
    mut boundary: impl FnMut(usize) -> io::Result<()>,
) -> io::Result<()> {
    if writes.is_empty() {
        return Ok(());
    }
    let plan = Plan { format: 1, writes };
    validate(root, &plan)?;
    let pending = root.join(DIRECTORY).join("pending.json");
    safe(root, &pending)?;
    if pending.exists() {
        return Err(bad());
    }
    fileio::atomic_write(
        &pending,
        &serde_json::to_string(&plan).map_err(|_| bad())?,
        true,
    )?;
    sync_parents(root, &pending)?;
    boundary(0)?;
    for (i, write) in plan.writes.iter().enumerate() {
        apply(root, write)?;
        boundary(i + 1)?;
    }
    fs::remove_file(&pending)?;
    sync_parents(root, &pending)
}

/// Called under the root lock by every public storage operation.
pub fn recover(root: &Path) -> io::Result<()> {
    let directory = root.join(DIRECTORY);
    safe(root, &directory)?;
    let pending = directory.join("pending.json");
    safe(root, &pending)?;
    if pending.exists() {
        let plan: Plan = serde_json::from_slice(&fs::read(&pending)?).map_err(|_| bad())?;
        validate(root, &plan)?;
        for write in &plan.writes {
            apply(root, write)?;
        }
        fs::remove_file(&pending)?;
        sync_parents(root, &pending)?;
    }
    clean_temps(&directory)
}

#[cfg(test)]
mod tests;
