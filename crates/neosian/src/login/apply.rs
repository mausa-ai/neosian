//! Install a validated assignment under the manual profile lock. The journal
//! carries the final public configuration. The newly issued secret is staged
//! separately, in the same private directory as the current credential.
use super::{
    device::Credential,
    host::Result,
    local::{self, State},
};
use crate::profile::{
    Profile,
    files::{self, Binding, Endpoint, Project, Registry},
    model::{CaptureMode, Field},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

#[derive(Deserialize, Serialize)]
struct Plan {
    before: super::redo::Before,
    removed: BTreeSet<String>,
    next: Option<State>,
    profiles: BTreeMap<String, Profile>,
    registry: Registry,
    staged: bool,
    retiring: bool,
}

pub(super) fn signature(
    bytes: &[u8],
    bindings: Option<&BTreeMap<String, Binding>>,
) -> Result<String> {
    let bindings = serde_json::to_vec(&bindings).map_err(|_| "cannot encode source bindings")?;
    Ok(local::digest([bytes, &bindings].concat()))
}

fn bindings(profile: &Profile, url: &str) -> BTreeMap<String, Binding> {
    profile
        .sources
        .iter()
        .map(|source| {
            let history = profile
                .history
                .as_ref()
                .is_some_and(|h| h.source == *source);
            let binding = Binding {
                endpoint: Endpoint::Http {
                    url: url.to_owned(),
                    token_env: local::TOKEN_ENV.into(),
                },
                capture: if history {
                    profile.capture.mode
                } else {
                    CaptureMode::Off
                },
            };
            (source.clone(), binding)
        })
        .collect()
}

pub fn capture(profiles: &mut BTreeMap<String, Profile>, permitted: bool) -> Result<bool> {
    let mut recording = false;
    for profile in profiles.values_mut() {
        if !permitted
            || profile
                .capture
                .fields
                .iter()
                .any(|field| !matches!(field, Field::Prompt | Field::Assistant))
        {
            profile.capture.mode = CaptureMode::Off;
        }
        if profile.capture.mode == CaptureMode::Local {
            return Err("hosted capture requires an HTTP history source".into());
        }
        recording |= profile.capture.mode == CaptureMode::Shared;
    }
    Ok(recording)
}

pub fn install(
    home: &Path,
    old: Option<&State>,
    mut next: State,
    profiles: BTreeMap<String, Profile>,
    project: Option<Project>,
    credential: Option<&Credential>,
) -> Result<bool> {
    let _lock = files::lock(home)?;
    let (before, mut registry, removed) = preflight(home, old, &profiles)?;
    next.projects = old
        .iter()
        .flat_map(|s| &s.projects)
        .filter(|p| {
            registry
                .projects
                .iter()
                .any(|r| r.root == p.root && r.profile == p.profile)
        })
        .cloned()
        .collect();
    if let Some(project) = project {
        if let Some(prior) = registry
            .projects
            .iter_mut()
            .find(|p| p.root == project.root)
        {
            if prior.profile != project.profile {
                let owned = next
                    .projects
                    .iter_mut()
                    .find(|p| p.root == prior.root && p.profile == prior.profile)
                    .ok_or("project root already has a manual binding")?;
                *owned = project.clone();
                *prior = project;
            }
        } else {
            next.projects.push(project.clone());
            registry.projects.push(project);
        }
    }
    for (name, profile) in &profiles {
        let sources = bindings(profile, &next.state_url);
        let encoded = crate::profile::encode(profile)?;
        next.installed
            .insert(name.clone(), signature(encoded.as_bytes(), Some(&sources))?);
        registry.profiles.insert(name.clone(), sources);
    }
    let rotating = old.filter(|s| {
        !s.signed_out
            && s.credential_source == local::Source::File
            && s.credential_digest != next.credential_digest
    });
    let previous = rotating.and_then(|s| s.stored(home).ok());
    let old_unavailable = rotating.is_some() && previous.is_none();
    Plan {
        before,
        next: Some(next),
        removed,
        profiles,
        registry,
        staged: credential.is_some(),
        retiring: previous.is_some(),
    }
    .commit(home, credential, previous.as_ref())?;
    Ok(old_unavailable)
}

pub fn forget(home: &Path, state: &State) -> Result<()> {
    let _lock = files::lock(home)?;
    let profiles = BTreeMap::new();
    let (before, registry, removed) = preflight(home, Some(state), &profiles)?;
    Plan {
        before,
        next: None,
        removed,
        profiles,
        registry,
        staged: false,
        retiring: false,
    }
    .commit(home, None, None)
}

fn preflight(
    home: &Path,
    old: Option<&State>,
    profiles: &BTreeMap<String, Profile>,
) -> Result<(super::redo::Before, Registry, BTreeSet<String>)> {
    let mut registry = files::registry(home)?;
    let mut before = super::redo::Before::new(home, &registry);
    for name in profiles.keys() {
        let path = files::path(home, name)?;
        if old.is_none_or(|s| !s.managed.contains(name))
            && (path.exists()
                || registry.profiles.contains_key(name)
                || registry.projects.iter().any(|p| p.profile == *name))
        {
            return Err(format!(
                "local profile {name} already exists; choose a separate neosian home"
            ));
        }
    }
    let mut removed = BTreeSet::new();
    if let Some(s) = old {
        for name in &s.managed {
            let path = files::path(home, name)?;
            let bytes =
                fs::read(path).map_err(|_| format!("cannot verify installed profile {name}"))?;
            if s.installed.get(name) != Some(&signature(&bytes, registry.profiles.get(name))?) {
                return Err(format!(
                    "managed profile {name} or its bindings changed locally; restore the assigned configuration before updating"
                ));
            }
            before.profile(name, &bytes);
            if !profiles.contains_key(name) {
                if registry.projects.iter().any(|p| {
                    p.profile == *name
                        && !s
                            .projects
                            .iter()
                            .any(|owned| owned.profile == *name && owned.root == p.root)
                }) {
                    return Err(format!(
                        "a manual project binding uses {name}; rebind it before removing this assignment"
                    ));
                }
                removed.insert(name.clone());
                registry.profiles.remove(name);
                registry.projects.retain(|p| p.profile != *name);
            }
        }
    }
    Ok((before, registry, removed))
}

impl Plan {
    fn commit(
        self,
        home: &Path,
        credential: Option<&Credential>,
        previous: Option<&Credential>,
    ) -> Result<()> {
        if toml::to_string_pretty(&self.registry)
            .map_err(|_| "cannot encode registry")?
            .len()
            > 1_048_576
        {
            return Err("profile bindings exceed 1 MiB".into());
        }
        local::path(home, local::CREDENTIAL)?;
        if let Some(cred) = credential {
            local::write_secret(home, local::STAGED, cred)?;
        }
        if let Some(previous) = previous {
            local::write_secret(home, super::retire::STAGED, previous)?;
        }
        if let Err(error) = local::write(home, local::JOURNAL, &self) {
            local::remove(home, local::STAGED)?;
            local::remove(home, super::retire::STAGED)?;
            return Err(error);
        }
        self.finish(home)
    }

    fn finish(&self, home: &Path) -> Result<()> {
        let registry =
            self.before
                .reconcile(home, &self.registry, &self.profiles, &self.removed)?;
        for name in &self.removed {
            match fs::remove_file(files::path(home, name)?) {
                Ok(()) => (),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
                Err(_) => return Err("cannot remove obsolete managed profile".into()),
            }
        }
        for (name, profile) in &self.profiles {
            files::write(home, name, profile, true)?;
        }
        files::write_registry(home, &registry)?;
        if let Some(s) = &self.next {
            if self.staged {
                let staged = local::path(home, local::STAGED)?;
                if staged.exists() {
                    fs::rename(staged, local::path(home, local::CREDENTIAL)?)
                        .map_err(|_| "cannot activate private credential")?;
                }
            } else if s.credential_source == local::Source::Environment {
                local::remove(home, local::CREDENTIAL)?;
            }
            local::write(home, local::STATE, s)?;
        } else {
            local::remove(home, local::CREDENTIAL)?;
            local::remove(home, local::STATE)?;
        }
        if self.retiring {
            super::retire::activate(home)?;
        }
        local::remove(home, local::JOURNAL)
    }
}

pub fn recover(home: &Path) -> Result<()> {
    let _lock = files::lock(home)?;
    if let Some(plan) = local::read::<Plan>(home, local::JOURNAL)? {
        plan.finish(home)?;
    }
    local::remove(home, super::retire::STAGED)?;
    local::remove(home, local::STAGED)
}
