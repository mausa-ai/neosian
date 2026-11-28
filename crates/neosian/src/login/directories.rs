//! Imported projects back to their directories (the L1d slice of the L1
//! plan, section F's ruling on directory resolution). The host's result
//! maps each source scope to `/me` or to an assigned profile and carries no
//! local path; everything here is worked out on this machine, and nothing
//! here is sent to a host.
//!
//! - **The map.** Each target is checked against the scopes the commit
//!   listed and the assignments refreshed after the host's success. A scope
//!   the commit did not list, a workspace without a profile, a profile the
//!   account is not assigned now and a scope named twice bind nothing and
//!   are told. What passes is kept in the account's login state; a later
//!   refresh drops every entry whose profile is no longer assigned.
//! - **This directory.** The canonical directory login runs for (`--project`
//!   or the working directory) is bound, exactly and never as a prefix, to
//!   the profile its own historical scope (`home::project_scope`) maps to,
//!   unless a registered binding already covers it; that binding is kept.
//! - **Other directories.** Selection resolves a directory the first time
//!   it is used, through the same function and map, after an explicit
//!   `--profile`, `NEOSIAN_PROFILE` and every registered binding, and before
//!   the default profile. Directories with the same name shared one
//!   historical scope on this machine, so they share its workspace.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::host::{Machine, Result};
use super::import::Target;
use super::local::State;
use super::output::say;
use crate::profile::files::{self, Project};

/// What successful imports left for selection, in the login state of the
/// account they belong to. Empty, and absent from the file, otherwise.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct Imported {
    /// Source scope to assigned profile.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub scopes: BTreeMap<String, String>,
    /// Canonical directory to assigned profile: exact, never a prefix.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub directories: BTreeMap<String, String>,
}

impl Imported {
    pub fn is_empty(&self) -> bool {
        self.scopes.is_empty() && self.directories.is_empty()
    }

    /// The entries whose profile is still assigned.
    fn kept(&self, assigned: impl Fn(&str) -> bool) -> Self {
        let keep = |map: &BTreeMap<String, String>| {
            map.iter()
                .filter(|(_, profile)| assigned(profile))
                .map(|(key, profile)| (key.clone(), profile.clone()))
                .collect()
        };
        Self {
            scopes: keep(&self.scopes),
            directories: keep(&self.directories),
        }
    }

    /// The profile a directory without a registered binding resolves to:
    /// its exact binding from an import, else its historical scope in the
    /// map; only a profile still assigned.
    pub fn select(&self, managed: &BTreeSet<String>, project: &Path) -> Option<&str> {
        if self.is_empty() {
            return None;
        }
        let dir = project
            .canonicalize()
            .unwrap_or_else(|_| project.to_path_buf());
        let assigned = |profile: &&String| managed.contains(profile.as_str());
        let exact = dir.to_str().and_then(|d| self.directories.get(d));
        exact
            .filter(assigned)
            .or_else(|| {
                let scope = crate::home::project_scope(&dir).ok()?;
                self.scopes.get(&scope).filter(assigned)
            })
            .map(String::as_str)
    }
}

/// An import the host reported succeeded, as its journal keeps it.
pub(crate) struct Finish {
    pub account: String,
    /// The scope units the commit listed.
    pub scopes: BTreeSet<String>,
    pub targets: Vec<Target>,
    /// `--project`, else the working directory.
    pub directory: PathBuf,
}

fn unbound(target: &Target, reason: &str) -> Value {
    json!({"source": target.source, "profile": target.profile, "reason": reason})
}

/// The validated map, and every workspace target that binds nothing.
fn map(finish: &Finish, assigned: impl Fn(&str) -> bool) -> (BTreeMap<String, String>, Vec<Value>) {
    let mut mapped = BTreeMap::new();
    let mut left = Vec::new();
    let mut twice = BTreeSet::new();
    for target in &finish.targets {
        let reason = if !finish.scopes.contains(&target.source) {
            "unknown"
        } else if target.mount == "me" {
            continue;
        } else {
            match target.profile.as_deref() {
                None => "unnamed",
                Some(p) if !assigned(p) => "unassigned",
                Some(p) => match mapped.insert(target.source.clone(), p.to_string()) {
                    Some(other) if other != p => {
                        twice.insert(target.source.clone());
                        "twice"
                    }
                    _ => continue,
                },
            }
        };
        left.push(unbound(target, reason));
    }
    mapped.retain(|scope, _| !twice.contains(scope));
    (mapped, left)
}

/// What becomes of the directory login runs for.
fn this_directory(
    dir: &Path,
    scopes: &BTreeMap<String, String>,
    registry: &[Project],
    chosen: Option<&Project>,
    bound: &mut BTreeMap<String, String>,
) -> Value {
    let Ok(dir) = dir.canonicalize() else {
        return Value::Null;
    };
    let root = dir.to_string_lossy();
    let scope = crate::home::project_scope(&dir).ok();
    let covering = registry
        .iter()
        .chain(chosen)
        .filter(|p| dir.starts_with(&p.root))
        .max_by_key(|p| p.root.components().count());
    if let Some(binding) = covering {
        return json!({"root": root, "scope": scope, "profile": binding.profile, "bound": "kept"});
    }
    let profile = scope.as_ref().and_then(|s| scopes.get(s));
    match (profile, dir.to_str()) {
        (Some(profile), Some(key)) => {
            bound.insert(key.to_string(), profile.clone());
            json!({"root": root, "scope": scope, "profile": profile, "bound": "import"})
        }
        _ => json!({"root": root, "scope": scope, "profile": null, "bound": "none"}),
    }
}

/// The imports the next login state carries and, when an import is being
/// finished, what finishing did. `chosen` is the checkout this login binds.
pub(super) fn settle(
    home: &Path,
    prior: Option<&State>,
    doc: &Machine,
    finishing: Option<&Finish>,
    chosen: Option<&Project>,
) -> Result<(Imported, Option<Value>)> {
    let assigned = |profile: &str| doc.profiles.contains_key(profile);
    let mut imported = prior.map(|s| s.imported.kept(assigned)).unwrap_or_default();
    let Some(finish) = finishing else {
        return Ok((imported, None));
    };
    if doc.account.id != finish.account {
        return Err(say("import_other_account"));
    }
    let (mapped, unbound) = map(finish, assigned);
    imported.scopes.extend(mapped.clone());
    let registry = files::registry(home)?;
    let directory = this_directory(
        &finish.directory,
        &imported.scopes,
        &registry.projects,
        chosen,
        &mut imported.directories,
    );
    let summary = json!({"status": "succeeded", "finished": true, "directory": directory,
        "unbound": unbound, "mapped": mapped.len(), "default": doc.default_profile});
    Ok((imported, Some(summary)))
}

#[cfg(test)]
#[path = "directories_tests.rs"]
mod tests;
