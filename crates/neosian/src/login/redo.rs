//! Compare the old and intended values during recovery. Unchanged registry
//! keys belong to their current owner. A third value in a changed key is a
//! conflict that must be resolved before any file is touched.
use super::{host::Result, local::digest};
use crate::profile::{
    self, Profile,
    files::{self, Project, Registry},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

#[derive(Serialize, Deserialize)]
pub struct Before {
    registry: Registry,
    registry_existed: bool,
    files: BTreeMap<String, String>,
}

pub fn file_hash(path: &Path) -> Result<Option<String>> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(digest(bytes))),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err("cannot inspect profile before recovery".into()),
    }
}

fn merge<K: Ord + Clone, V: PartialEq + Clone>(
    prior: &BTreeMap<K, V>,
    wanted: &BTreeMap<K, V>,
    current: &mut BTreeMap<K, V>,
) -> Result<()> {
    for key in prior.keys().chain(wanted.keys()).collect::<BTreeSet<_>>() {
        let (before, after, live) = (prior.get(key), wanted.get(key), current.get(key));
        if before == after || after == live {
            continue;
        }
        if before != live {
            return Err("profile binding was edited during an interrupted login; restore its previous or intended value and retry".into());
        }
        if let Some(value) = after {
            current.insert(key.clone(), value.clone());
        } else {
            current.remove(key);
        }
    }
    Ok(())
}

fn roots(registry: &Registry) -> BTreeMap<std::path::PathBuf, String> {
    registry
        .projects
        .iter()
        .map(|p| (p.root.clone(), p.profile.clone()))
        .collect()
}

impl Before {
    pub fn new(home: &Path, registry: &Registry) -> Self {
        Self {
            registry: registry.clone(),
            registry_existed: home.join("profile-bindings.toml").exists(),
            files: BTreeMap::new(),
        }
    }
    pub fn profile(&mut self, name: &str, bytes: &[u8]) {
        self.files.insert(name.to_owned(), digest(bytes));
    }

    pub fn reconcile(
        &self,
        home: &Path,
        wanted: &Registry,
        profiles: &BTreeMap<String, Profile>,
        removed: &BTreeSet<String>,
    ) -> Result<Registry> {
        for name in profiles.keys().chain(removed) {
            let live = file_hash(&files::path(home, name)?)?;
            let after = profiles
                .get(name)
                .map(profile::encode)
                .transpose()?
                .map(digest);
            if live.as_ref() != self.files.get(name) && live != after {
                return Err(format!(
                    "profile {name} was edited during an interrupted login; restore its previous or intended contents and retry"
                ));
            }
        }
        let mut live = if home.join("profile-bindings.toml").exists() {
            files::registry(home)?
        } else if self.registry_existed {
            return Err("the binding registry was removed during an interrupted login; restore it and retry".into());
        } else {
            self.registry.clone()
        };
        if self.registry_existed && live.installation_id != self.registry.installation_id {
            return Err("the installation identity changed during an interrupted login".into());
        }
        // A previously unchanged managed source is still part of the
        // assignment. Publishing new ownership receipts over local edits
        // would leave a broken assignment, even if no bind write was needed.
        for name in profiles.keys() {
            let source = live.profiles.get(name);
            if source != self.registry.profiles.get(name) && source != wanted.profiles.get(name) {
                return Err(format!(
                    "managed bindings for {name} changed during login; restore them and retry"
                ));
            }
        }
        merge(
            &self.registry.profiles,
            &wanted.profiles,
            &mut live.profiles,
        )?;
        let mut projects = roots(&live);
        merge(&roots(&self.registry), &roots(wanted), &mut projects)?;
        if projects.values().any(|name| removed.contains(name)) {
            return Err(
                "a new manual checkout still uses a removed assignment; rebind it before recovery"
                    .into(),
            );
        }
        live.projects = projects
            .into_iter()
            .map(|(root, profile)| Project { root, profile })
            .collect();
        if toml::to_string_pretty(&live)
            .map_err(|_| "cannot encode registry")?
            .len()
            > 1_048_576
        {
            return Err("profile bindings exceed 1 MiB".into());
        }
        Ok(live)
    }
}
