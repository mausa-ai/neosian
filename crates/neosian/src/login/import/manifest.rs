//! A snapshot's manifest: every file of the copy, by its path relative to
//! the copy (`/`-separated, sorted), with its length and SHA-256. The
//! copy's own store lock, which every read of the copy holds, is left out.
//! The snapshot's id is the SHA-256 of the file list alone, so two takes of
//! the same home name the same content.

use std::fs::{self, File};
use std::io::{self, Read};
use std::path::Path;

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::snapshot::Fault;
use crate::store::time::Stamp;

/// The copy's files: path, length and SHA-256, sorted by path.
pub(crate) type Listed = Vec<(String, u64, String)>;

const LOCK: &str = ".store.lock";

fn hex(raw: &[u8]) -> String {
    raw.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn digest(path: &Path) -> io::Result<(u64, String)> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = vec![0; 64 * 1024];
    let mut length = 0;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            return Ok((length, hex(&hash.finalize())));
        }
        hash.update(&buffer[..read]);
        length += read as u64;
    }
}

/// Lists every file under `root`; a link or a special file there means the
/// copy was changed.
pub(crate) fn list(root: &Path) -> Result<Listed, Fault> {
    fn walk(dir: &Path, prefix: &str, found: &mut Listed) -> Result<(), Fault> {
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let path = format!("{prefix}{name}");
            let kind = entry.file_type()?;
            if kind.is_dir() {
                walk(&entry.path(), &format!("{path}/"), found)?;
            } else if kind.is_file() {
                if path != LOCK {
                    let (length, sha256) = digest(&entry.path())?;
                    found.push((path, length, sha256));
                }
            } else {
                return Err(Fault::Changed { path });
            }
        }
        Ok(())
    }
    let mut found = Listed::new();
    walk(root, "", &mut found)?;
    found.sort();
    Ok(found)
}

fn files(listed: &Listed) -> Value {
    Value::Array(
        listed
            .iter()
            .map(|(path, length, sha256)| json!([path, length, sha256]))
            .collect(),
    )
}

/// The SHA-256 of the file list's compact JSON.
pub(crate) fn id(listed: &Listed) -> String {
    hex(&Sha256::digest(files(listed).to_string().as_bytes()))
}

pub(crate) fn render(taken_at: Stamp, listed: &Listed) -> String {
    let manifest = json!({"format": 1, "taken_at": taken_at.z(), "files": files(listed)});
    format!("{manifest}\n")
}

/// The time and file list of a manifest; None for anything else.
pub(crate) fn parse(text: &str) -> Option<(Stamp, Listed)> {
    let value: Value = serde_json::from_str(text).ok()?;
    if value["format"] != 1 {
        return None;
    }
    let taken_at = Stamp::parse(value["taken_at"].as_str()?).ok()?;
    let mut listed = Listed::new();
    for file in value["files"].as_array()? {
        let path = file[0].as_str()?.to_string();
        listed.push((path, file[1].as_u64()?, file[2].as_str()?.to_string()));
    }
    Some((taken_at, listed))
}

/// The first path where two lists differ: a file changed, missing or added.
pub(crate) fn first_difference(recorded: &Listed, found: &Listed) -> Option<String> {
    let mut paired = recorded.iter().zip(found.iter());
    if let Some((was, now)) = paired.find(|(was, now)| was != now) {
        return Some(was.0.clone().min(now.0.clone()));
    }
    let longer = if recorded.len() > found.len() {
        recorded
    } else {
        found
    };
    longer
        .get(recorded.len().min(found.len()))
        .map(|file| file.0.clone())
}
