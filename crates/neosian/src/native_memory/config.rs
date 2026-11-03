//! Local consent and routing, deliberately outside portable profiles and archives.

use super::{digest, invalid};
use crate::store::error::Error;
use crate::store::{fileio, grammar};
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

pub const MAX_SOURCES: usize = 16;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub name: String,
    pub agent: String,
    pub source: PathBuf,
    pub recursive: bool,
    pub project: Option<PathBuf>,
    pub store: PathBuf,
    pub scope: String,
    pub destination: String,
}

impl Source {
    pub fn id(&self) -> String {
        digest(&serde_json::to_vec(self).expect("source is serializable"))
    }

    pub fn matches(&self, agent: &str, project: &Path) -> bool {
        self.agent == agent && self.project.as_ref().is_none_or(|p| project.starts_with(p))
    }

    fn validate(&self) -> Result<(), Error> {
        name(&self.name)?;
        if !matches!(self.agent.as_str(), "claude-code" | "codex") {
            return Err(invalid("native memory agent must be claude-code or codex"));
        }
        grammar::parse_scope(&self.scope)?;
        let stripped = self
            .destination
            .strip_prefix('/')
            .ok_or_else(|| invalid("invalid native memory destination"))?;
        grammar::validate_document_path(stripped)?;
        let (_, suffix) = stripped
            .split_once('/')
            .ok_or_else(|| invalid("invalid native memory destination"))?;
        if suffix != format!("native-memory/{}", self.name)
            || !self.source.is_absolute()
            || !self.store.is_absolute()
            || self.project.as_ref().is_some_and(|p| !p.is_absolute())
        {
            return Err(invalid("invalid native memory registration"));
        }
        if self.source.starts_with(&self.store) || self.store.starts_with(&self.source) {
            return Err(invalid(
                "native memory source and destination store must not overlap",
            ));
        }
        Ok(())
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Registry {
    pub format: u32,
    pub sources: Vec<Source>,
}

pub fn name(value: &str) -> Result<(), Error> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err(invalid(
            "native memory names use 1-64 ASCII letters, digits, '-' or '_'",
        ));
    }
    Ok(())
}

pub fn absolute(path: &Path) -> Result<PathBuf, Error> {
    if path.to_str().is_none() {
        return Err(invalid("native memory paths must be UTF-8"));
    }
    let full = crate::profile::resolve::absolute(path).map_err(invalid)?;
    // Resolve existing parents as well, including macOS's /var and /tmp aliases.
    if full.exists() {
        let canonical = full.canonicalize()?;
        if canonical.to_str().is_none() {
            return Err(invalid("native memory paths must be UTF-8"));
        }
        return Ok(canonical);
    }
    let parent = full.parent().ok_or_else(|| invalid("path has no parent"))?;
    Ok(absolute(parent)?.join(
        full.file_name()
            .ok_or_else(|| invalid("path has no filename"))?,
    ))
}

pub fn path() -> Result<PathBuf, Error> {
    Ok(crate::home::home()?.join("native-memory.json"))
}

pub fn read() -> Result<Registry, Error> {
    let path = path()?;
    let file = match fs::File::open(&path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(Registry {
                format: 1,
                sources: vec![],
            });
        }
        Err(e) => return Err(e.into()),
    };
    let mut text = String::new();
    file.take(128 * 1024 + 1).read_to_string(&mut text)?;
    if text.len() > 128 * 1024 {
        return Err(invalid("native memory registry exceeds 128 KiB"));
    }
    let result: Registry =
        serde_json::from_str(&text).map_err(|_| invalid("invalid native memory registry"))?;
    if result.format != 1 || result.sources.len() > MAX_SOURCES {
        return Err(invalid(
            "unsupported native memory registry format or source count",
        ));
    }
    let mut names = std::collections::BTreeSet::new();
    for source in &result.sources {
        source.validate()?;
        if !names.insert(&source.name) {
            return Err(invalid("duplicate native memory registration"));
        }
    }
    Ok(result)
}

pub fn write(registry: &Registry) -> Result<(), Error> {
    let path = path()?;
    fileio::private_mkdir(path.parent().expect("registry has parent"))?;
    let text = serde_json::to_string_pretty(registry).map_err(std::io::Error::other)?;
    if text.len() > 128 * 1024 {
        return Err(invalid("native memory registry exceeds 128 KiB"));
    }
    fileio::atomic_write(&path, &text, true)?;
    Ok(())
}
