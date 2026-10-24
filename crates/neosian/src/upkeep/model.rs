//! Bounded review data. An approval is supplied separately from a proposal.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::knowledge::model::{Reference, digest, invalid};
use crate::store::error::Error;

pub const MAX_PLAN: usize = 256 * 1024;
pub const MAX_CHANGES: usize = 32;
pub const MAX_GUARDS: usize = 128;
pub const MAX_SCAN: usize = 1000;
pub const MAX_SCAN_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    Replace,
    Restore,
    Delete,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub format: u32,
    pub changes: Vec<Edit>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Edit {
    pub path: String,
    pub operation: Operation,
    pub expected_version: u64,
    pub reason: String,
    #[serde(default)]
    pub body: Option<String>,
    #[serde(default)]
    pub attributes: Option<Map<String, Value>>,
    #[serde(default)]
    pub references: Option<Vec<Reference>>,
    #[serde(default)]
    pub restore_version: Option<u64>,
    #[serde(default)]
    pub sources: Vec<String>,
    #[serde(default)]
    pub keeper: Option<String>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Guard {
    pub path: String,
    pub scope: String,
    pub source: Option<String>,
    pub version: Option<u64>,
    pub sha256: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Change {
    pub path: String,
    pub operation: Operation,
    pub reason: String,
    pub content: Option<String>,
    pub keeper: Option<String>,
    pub restore_version: Option<u64>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    pub format: u32,
    pub workspace: Option<String>,
    pub observations: Vec<Guard>,
    pub changes: Vec<Change>,
    pub id: String,
}

pub fn parse<T: serde::de::DeserializeOwned>(text: &str) -> Result<T, Error> {
    if text.len() > MAX_PLAN {
        return Err(invalid("upkeep input exceeds 256 KiB"));
    }
    crate::knowledge::json::parse(text)
}

pub fn reason(text: &str) -> Result<(), Error> {
    if text.trim().is_empty() || text.chars().count() > 240 || text.contains('\0') {
        return Err(invalid(
            "upkeep reason requires 1..240 characters without NUL",
        ));
    }
    Ok(())
}

fn count(format: u32, count: usize) -> Result<(), Error> {
    if format != 1 || !(1..=MAX_CHANGES).contains(&count) {
        return Err(invalid("upkeep requires format 1 and 1..32 changes"));
    }
    Ok(())
}

impl Request {
    pub fn validate(&self) -> Result<(), Error> {
        count(self.format, self.changes.len())?;
        let mut paths = BTreeSet::new();
        for change in &self.changes {
            reason(&change.reason)?;
            if change.expected_version == 0 || !paths.insert(&change.path) {
                return Err(invalid(
                    "targets must be distinct with positive expected versions",
                ));
            }
            if change.sources.len() > 16 {
                return Err(invalid("each change permits at most 16 evidence sources"));
            }
            let valid = match change.operation {
                Operation::Replace => {
                    change.restore_version.is_none()
                        && change.keeper.is_none()
                        && (change.body.is_some()
                            || change.attributes.is_some()
                            || change.references.is_some())
                }
                Operation::Restore => {
                    change.restore_version.is_some_and(|v| v > 0)
                        && change.body.is_none()
                        && change.attributes.is_none()
                        && change.references.is_none()
                        && change.keeper.is_none()
                }
                Operation::Delete => {
                    change.body.is_none()
                        && change.attributes.is_none()
                        && change.references.is_none()
                        && change.restore_version.is_none()
                        && change.sources.is_empty()
                }
            };
            if !valid {
                return Err(invalid(
                    "upkeep operation has missing or incompatible fields",
                ));
            }
        }
        Ok(())
    }
}

impl Proposal {
    pub fn digest(&self) -> String {
        let mut value = serde_json::to_value(self).expect("proposal serializes");
        value.as_object_mut().expect("proposal object").remove("id");
        value.sort_all_objects();
        digest(serde_json::to_vec(&value).expect("proposal serializes"))
    }

    pub fn validate(&self) -> Result<(), Error> {
        count(self.format, self.changes.len())?;
        if self.id != self.digest()
            || serde_json::to_vec(self).expect("proposal serializes").len() > MAX_PLAN
        {
            return Err(invalid(
                "upkeep proposal digest differs or proposal exceeds 256 KiB",
            ));
        }
        if self.observations.is_empty() || self.observations.len() > MAX_GUARDS {
            return Err(invalid("upkeep requires 1..128 observations"));
        }
        let mut paths = BTreeSet::new();
        for guard in &self.observations {
            if !paths.insert(&guard.path)
                || guard.version == Some(0)
                || guard.sha256.len() != 64
                || !guard
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            {
                return Err(invalid("invalid or duplicate upkeep observation"));
            }
        }
        let mut targets = BTreeSet::new();
        for change in &self.changes {
            reason(&change.reason)?;
            if !targets.insert(&change.path) || !paths.contains(&change.path) {
                return Err(invalid("each distinct target requires an observation"));
            }
            let valid = match change.operation {
                Operation::Replace => {
                    change.content.is_some()
                        && change.keeper.is_none()
                        && change.restore_version.is_none()
                }
                Operation::Restore => {
                    change.content.is_some()
                        && change.keeper.is_none()
                        && change.restore_version.is_some_and(|v| v > 0)
                }
                Operation::Delete => {
                    change.content.is_none()
                        && change.restore_version.is_none()
                        && change
                            .keeper
                            .as_ref()
                            .is_none_or(|p| p != &change.path && paths.contains(p))
                }
            };
            if !valid {
                return Err(invalid("invalid upkeep proposal operation"));
            }
        }
        if self
            .changes
            .iter()
            .any(|c| c.keeper.as_ref().is_some_and(|p| targets.contains(p)))
        {
            return Err(invalid(
                "a duplicate keeper cannot be changed in the same proposal",
            ));
        }
        Ok(())
    }
}
