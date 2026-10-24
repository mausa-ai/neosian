//! One complete selection: explicit name, environment, then longest local project root.

use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use super::Profile;
use super::files::{self, Binding, Endpoint};

pub struct Resolved {
    pub name: String,
    pub profile: Profile,
    pub bindings: BTreeMap<String, Binding>,
    pub installation_id: String,
    pub home: PathBuf,
}

pub fn absolute(path: &Path) -> Result<PathBuf, String> {
    let cwd = std::env::current_dir().map_err(|_| "cannot read working directory")?;
    Ok(crate::clients::context::absolute(&cwd, path))
}

pub fn selected(explicit: Option<&str>, project: &Path) -> Result<Option<Resolved>, String> {
    let home = files::home()?;
    let registry = files::registry(&home)?;
    let named = std::env::var("NEOSIAN_PROFILE")
        .ok()
        .filter(|s| !s.is_empty());
    let project = absolute(project)?;
    let local = registry
        .projects
        .iter()
        .filter(|p| project.starts_with(&p.root))
        .max_by_key(|p| p.root.components().count())
        .map(|p| p.profile.as_str());
    let Some(name) = explicit.or(named.as_deref()).or(local) else {
        return Ok(None);
    };
    let profile = files::read(&home, name)?;
    let bindings = registry.profiles.get(name).cloned().unwrap_or_default();
    Ok(Some(Resolved {
        name: name.into(),
        profile,
        bindings,
        installation_id: registry.installation_id,
        home,
    }))
}

impl Resolved {
    /// Validation without opening any source or exposing credential values.
    pub fn check(&self) -> Result<(), String> {
        for source in &self.profile.sources {
            self.bindings
                .get(source)
                .ok_or("profile source is unbound; use profile bind")?
                .validate()?;
        }
        let history = self.profile.history.as_ref();
        let mode = self.profile.capture.mode;
        if mode != super::model::CaptureMode::Off {
            let history = history.ok_or("capture requires history")?;
            let binding = &self.bindings[&history.source];
            if binding.capture != mode {
                return Err("local binding does not permit this capture mode".into());
            }
            if mode == super::model::CaptureMode::Local {
                let Endpoint::File { root } = &binding.endpoint else {
                    return Err("local capture needs files".into());
                };
                let root = absolute(root)?;
                for mount in &self.profile.mounts {
                    if let Endpoint::File { root: mounted } = &self.bindings[&mount.source].endpoint
                    {
                        let mounted = absolute(mounted)?;
                        if root.starts_with(&mounted) || mounted.starts_with(&root) {
                            return Err(
                                "local capture needs a separate, non-overlapping file root".into(),
                            );
                        }
                    }
                }
            }
        }
        Ok(())
    }

    pub fn conflicts(&self, explicit: bool) -> Result<(), String> {
        if explicit || std::env::var_os("NEOSIAN_SCOPE").is_some_and(|s| !s.is_empty()) {
            return Err("a profile cannot be combined with legacy store/scope selectors".into());
        }
        if std::env::var_os("NEOSIAN_POSTGRES_DSN").is_some_and(|s| !s.is_empty())
            && !self.bindings.values().any(|b| {
                matches!(&b.endpoint,
                Endpoint::Postgres { dsn_env, .. } if dsn_env == "NEOSIAN_POSTGRES_DSN")
            })
        {
            return Err("ambient NEOSIAN_POSTGRES_DSN conflicts with the profile".into());
        }
        Ok(())
    }

    pub fn report(&self) -> Value {
        let required: Vec<_> = self
            .profile
            .sources
            .iter()
            .filter_map(|s| self.bindings.get(s).and_then(|b| b.credential()))
            .collect();
        let missing: Vec<_> = required
            .iter()
            .filter(|name| std::env::var(name).ok().is_none_or(|v| v.is_empty()))
            .collect();
        let issue = self.conflicts(false).and_then(|()| self.check()).err();
        let sources: Vec<_> = self
            .profile
            .sources
            .iter()
            .map(|source| {
                let binding = self.bindings.get(source);
                let mut row = json!({"source":source,"bound":binding.is_some()});
                if let Some(binding) = binding {
                    row["binding"] = json!(binding);
                }
                row
            })
            .collect();
        json!({"profile":self.name,"workspace_id":self.profile.workspace_id,
            "definition":self.profile,"sources":sources,"ready":issue.is_none() && missing.is_empty(),
            "issue":issue,"capture":self.profile.capture.mode,
            "required_environment":required,"missing_environment":missing,
            "authorization":"checked_by_source_at_use","constrained_http_mcp":false})
    }
}
