//! Local operation budgets; these never widen backend authorization or context injection.

use std::cell::Cell;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use crate::store::{error::Error, fileio};

/// Carry a deliberate refusal through the redo writer's I/O interface without
/// exposing arbitrary filesystem/driver errors through the HTTP error envelope.
#[derive(Debug)]
pub struct Refusal(String);
impl std::fmt::Display for Refusal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for Refusal {}

pub fn io_refusal(message: String) -> std::io::Error {
    std::io::Error::other(Refusal(message))
}

pub fn into_io(error: Error) -> std::io::Error {
    match error {
        Error::Value(message) => io_refusal(message),
        Error::Io(error) => error,
        other => std::io::Error::other(format!("{other:?}")),
    }
}

pub fn setting(name: &str, default: u64, maximum: u64) -> Result<u64, Error> {
    let Some(value) = std::env::var_os(name).filter(|s| !s.is_empty()) else {
        return Ok(default);
    };
    value
        .to_str()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|n| *n > 0 && *n <= maximum)
        .ok_or_else(|| Error::Value(format!("{name} must be an integer in 1..{maximum}")))
}

pub fn check(name: &str, used: u64, maximum: u64) -> Result<(), Error> {
    if used > maximum {
        Err(Error::Value(format!(
            "resource limit {name} exceeded: {used} > {maximum}; select a smaller batch or raise this setting"
        )))
    } else {
        Ok(())
    }
}

pub const DOCUMENTS: &str = "NEOSIAN_KNOWLEDGE_MAX_DOCUMENTS";
pub const BYTES: &str = "NEOSIAN_KNOWLEDGE_MAX_BYTES";
pub const TEMP: &str = "NEOSIAN_MAX_TEMP_BYTES";
pub const REPLICA: &str = "NEOSIAN_REPLICA_MAX_BYTES";
pub const FILE: &str = "NEOSIAN_MAX_FILE_BYTES";
pub const RESPONSE: &str = "NEOSIAN_MAX_RESPONSE_BYTES";

#[derive(Clone, Copy)]
pub struct Knowledge {
    pub documents: u64,
    pub bytes: u64,
}

impl Knowledge {
    pub fn load() -> Result<Self, Error> {
        Ok(Self {
            documents: setting(DOCUMENTS, 10_000, 100_000)?,
            bytes: setting(BYTES, 256 << 20, 2 << 30)?,
        })
    }
    pub fn check(&self, documents: usize, bytes: usize) -> Result<(), Error> {
        check(DOCUMENTS, documents as u64, self.documents)?;
        check(BYTES, bytes as u64, self.bytes)
    }
    pub fn manifest(&self) -> usize {
        (self.bytes.min(64 << 20)) as usize
    }
}

pub fn file_bytes() -> Result<u64, Error> {
    setting(FILE, 64 << 20, 1 << 30)
}
pub fn temp_bytes() -> Result<u64, Error> {
    setting(TEMP, 512 << 20, 8 << 30)
}

pub fn read_file(path: &Path) -> Result<String, Error> {
    let maximum = file_bytes()?;
    check(FILE, fs::metadata(path)?.len(), maximum)?;
    let mut bytes = Vec::new();
    fs::File::open(path)?
        .take(maximum + 1)
        .read_to_end(&mut bytes)?;
    check(FILE, bytes.len() as u64, maximum)?;
    String::from_utf8(bytes).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidData, "file is not UTF-8").into()
    })
}

/// Prepared bodies live on disk; callers retain only names and bounded metadata.
pub struct Scratch {
    pub directory: PathBuf,
    bytes: Cell<u64>,
    maximum: u64,
}

impl Scratch {
    pub fn new() -> Result<Self, Error> {
        Self::beside(&std::env::temp_dir())
    }
    pub fn beside(parent: &Path) -> Result<Self, Error> {
        let maximum = temp_bytes()?;
        let directory = parent.join(format!(".neosian-stage-{}", uuid::Uuid::new_v4()));
        fileio::private_builder().create(&directory)?;
        Ok(Self {
            directory,
            bytes: Cell::new(0),
            maximum,
        })
    }
    pub fn remaining(&self) -> u64 {
        self.maximum - self.bytes.get()
    }
    pub fn save(&self, text: &str) -> Result<PathBuf, Error> {
        self.put(Path::new(&uuid::Uuid::new_v4().to_string()), text)
    }
    pub fn put(&self, relative: &Path, text: &str) -> Result<PathBuf, Error> {
        let used = self
            .bytes
            .get()
            .checked_add(text.len() as u64)
            .ok_or_else(|| Error::Value("temporary byte count overflow".into()))?;
        check(TEMP, used, self.maximum)?;
        if relative
            .components()
            .any(|c| !matches!(c, std::path::Component::Normal(_)))
        {
            return Err(Error::Value("staged path must be relative".into()));
        }
        let path = self.directory.join(relative);
        fileio::private_mkdir(path.parent().expect("staged parent"))?;
        let mut file = fileio::private().write(true).create_new(true).open(&path)?;
        file.write_all(text.as_bytes())?;
        self.bytes.set(used);
        Ok(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}
