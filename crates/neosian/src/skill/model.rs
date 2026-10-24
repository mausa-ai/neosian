//! Managed skill details are versioned content, never authority or executable setup.

use std::collections::{BTreeMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::knowledge::model::{Content, digest, invalid, ordinary};
use crate::store::error::Error;

pub const MAX_FILES: usize = 64;
pub const MAX_NODES: usize = 64;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Revision {
    pub version: u64,
    pub sha256: String,
}

impl Revision {
    pub fn validate(&self) -> Result<(), Error> {
        if self.version == 0 || !hash(&self.sha256) {
            return Err(invalid(
                "revision requires a positive version and lowercase SHA-256",
            ));
        }
        Ok(())
    }
    pub fn matches(&self, version: u64, content: &str) -> bool {
        self.version == version && self.sha256 == digest(content)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Pin {
    pub source: String,
    pub scope: String,
    pub version: u64,
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Dependency {
    pub skill: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision: Option<Revision>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    pub format: u32,
    #[serde(default)]
    pub requires: Vec<Dependency>,
    #[serde(default)]
    pub files: BTreeMap<String, String>,
    #[serde(default)]
    pub collections: Vec<String>,
}

impl Default for Spec {
    fn default() -> Self {
        Self {
            format: 1,
            requires: vec![],
            files: BTreeMap::new(),
            collections: vec![],
        }
    }
}

pub fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn address(path: &str) -> Result<(&str, &str), Error> {
    let parts: Vec<_> = path.strip_prefix('/').unwrap_or("").split('/').collect();
    if parts.len() != 3 || parts[1] != "skills" {
        return Err(invalid("expected /mount/skills/name"));
    }
    crate::store::grammar::validate_document_path(parts[0])?;
    ordinary(&format!("skills/{}", parts[2]))?;
    Ok((parts[0], parts[2]))
}

pub fn selected(name: &str) -> Result<(), Error> {
    if let Some(builtin) = name.strip_prefix("builtin:") {
        if !["handoff", "upkeep"].contains(&builtin) {
            return Err(invalid("unknown built-in skill"));
        }
    } else {
        address(name)?;
    }
    Ok(())
}

pub fn dependency(mount: &str, name: &str) -> Result<String, Error> {
    if name.starts_with('/') || name.starts_with("builtin:") {
        selected(name)?;
        return Ok(name.into());
    }
    ordinary(&format!("skills/{name}"))?;
    if name.contains('/') {
        return Err(invalid("relative dependency must be one skill name"));
    }
    Ok(format!("/{mount}/skills/{name}"))
}

impl Spec {
    pub fn validate(&self) -> Result<(), Error> {
        if self.format != 1
            || self.requires.len() > 16
            || self.files.len() > MAX_FILES
            || self.collections.len() > 32
        {
            return Err(invalid(
                "skill format must be 1 with at most 16 dependencies, 64 files and 32 collections",
            ));
        }
        let mut names = HashSet::new();
        for item in &self.requires {
            let key = dependency("selected", &item.skill)?;
            if !names.insert(key) {
                return Err(invalid("duplicate skill dependency"));
            }
            if let Some(revision) = &item.revision {
                revision.validate()?;
                if item.skill.starts_with("builtin:") {
                    return Err(invalid("built-ins have no stored revision"));
                }
            }
        }
        names.clear();
        for (file, sha) in &self.files {
            ordinary(&format!("skills/resources/{file}"))?;
            if file.split('/').count() > 16
                || !names.insert(file.to_ascii_lowercase())
                || !hash(sha)
            {
                return Err(invalid("invalid or duplicate supporting filename/hash"));
            }
            if ["skill.md", "skill.json"].contains(
                &file
                    .split('/')
                    .next()
                    .unwrap_or("")
                    .to_ascii_lowercase()
                    .as_str(),
            ) {
                return Err(invalid("supporting file aliases a package control file"));
            }
        }
        let paths: Vec<_> = self.files.keys().collect();
        for (i, a) in paths.iter().enumerate() {
            for b in &paths[i + 1..] {
                if a.starts_with(&format!("{b}/")) || b.starts_with(&format!("{a}/")) {
                    return Err(invalid("supporting file is also a directory"));
                }
                for (x, y) in a.split('/').zip(b.split('/')) {
                    if !x.eq_ignore_ascii_case(y) {
                        break;
                    }
                    if x != y {
                        return Err(invalid("case-colliding supporting path components"));
                    }
                }
            }
        }
        names.clear();
        for collection in &self.collections {
            ordinary(collection)?;
            if !names.insert(collection.clone()) {
                return Err(invalid("duplicate collection association"));
            }
        }
        Ok(())
    }
}

pub fn parse(text: &str) -> Result<(Content, Spec), Error> {
    let content = Content::parse(text, "skills")?;
    let spec = match content.metadata.attributes.get("skill") {
        Some(value) => serde_json::from_value(value.clone())
            .map_err(|e| invalid(format!("skill metadata: {e}")))?,
        None => Spec::default(),
    };
    spec.validate()?;
    Ok((content, spec))
}
