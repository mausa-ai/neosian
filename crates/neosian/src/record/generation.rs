//! Cursor and the OpenCode adapter identify the native turn. A response can arrive
//! after the next prompt. Keep only digests locally and never spool that reply.

use super::payload::{PROMPT_EVENT, Payload};
use crate::store::fileio::atomic_write;
use sha2::{Digest, Sha256};
use std::{fs, io, path::Path};

fn observed(id: &str, payload: &Payload) -> Option<String> {
    let generation = payload
        .get("generation_id")?
        .as_str()
        .filter(|s| !s.is_empty())?;
    let mut hash = Sha256::new();
    hash.update(id.as_bytes());
    hash.update(generation.as_bytes());
    Some(hash.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

fn active(dir: &Path, id: &str) -> io::Result<Option<String>> {
    match fs::read_to_string(dir.join(format!("{id}.generation"))) {
        Ok(s) if s.len() == 64 && s.bytes().all(|c| c.is_ascii_hexdigit()) => Ok(Some(s)),
        Ok(_) => Err(io::Error::other("invalid local capture generation")),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e),
    }
}

fn deny(dir: &Path, id: &str, digest: &str) -> io::Result<()> {
    let path = dir.join(format!("{id}.{digest}.ignored"));
    if !path.try_exists()? {
        atomic_write(&path, "", true)?;
    }
    Ok(())
}

pub fn retire(dir: &Path, id: &str, key: &str) -> io::Result<()> {
    if let Some(previous) = active(dir, id)? {
        deny(dir, key, &previous)?;
        fs::remove_file(dir.join(format!("{id}.generation")))?;
    }
    Ok(())
}

pub fn ignore(dir: &Path, key: &str, payload: &Payload) -> io::Result<()> {
    if let Some(generation) = observed(key, payload) {
        deny(dir, key, &generation)?;
    }
    Ok(())
}

pub fn blocks(
    dir: &Path,
    id: &str,
    key: &str,
    event: &str,
    payload: &Payload,
    resumed: bool,
) -> io::Result<bool> {
    if !resumed && event != PROMPT_EVENT {
        return Ok(false);
    }
    let Some(generation) = observed(key, payload) else {
        return Ok(resumed);
    };
    if event != PROMPT_EVENT {
        return Ok(active(dir, id)?.as_ref() != Some(&generation));
    }
    if resumed
        && dir
            .join(format!("{key}.{generation}.ignored"))
            .try_exists()?
    {
        return Ok(true);
    }
    atomic_write(&dir.join(format!("{id}.generation")), &generation, true)?;
    Ok(false)
}
