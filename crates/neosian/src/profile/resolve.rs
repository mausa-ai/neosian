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
    pub project: PathBuf,
    pub login: Option<crate::login::local::State>,
}

pub fn absolute(path: &Path) -> Result<PathBuf, String> {
    let cwd = std::env::current_dir().map_err(|_| "cannot read working directory")?;
    Ok(crate::clients::context::absolute(&cwd, path))
}

pub fn selected(explicit: Option<&str>, project: &Path) -> Result<Option<Resolved>, String> {
    select(explicit, project, true)
}

pub fn without_default(explicit: Option<&str>, project: &Path) -> Result<Option<Resolved>, String> {
    select(explicit, project, false)
}

fn select(
    explicit: Option<&str>,
    project: &Path,
    default: bool,
) -> Result<Option<Resolved>, String> {
    let home = files::home()?;
    // Take a consistent snapshot when managed selections are being installed.
    let _lock = if home.join("login").exists() {
        Some(files::lock(&home)?)
    } else {
        None
    };
    let state = crate::login::local::state(&home)?;
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
    let Some(name) = explicit.or(named.as_deref()).or(local).or_else(|| {
        state
            .as_ref()
            .filter(|_| default)
            .map(|s| s.default_profile.as_str())
    }) else {
        return Ok(None);
    };
    let mut profile = files::read(&home, name)?;
    let bindings = registry.profiles.get(name).cloned().unwrap_or_default();
    let managed = state.as_ref().filter(|s| s.managed.contains(name));
    if let Some(s) = managed {
        s.verify_profile(name, &profile, &bindings)?;
        if crate::login::local::capture_off(&home)? {
            profile.capture.mode = super::model::CaptureMode::Off;
        }
    }
    Ok(Some(Resolved {
        name: name.into(),
        login: managed.cloned(),
        profile,
        bindings,
        installation_id: registry.installation_id,
        home,
        project,
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

    pub fn credential(&self, source: &str) -> Result<Option<String>, String> {
        let Some(binding) = self.bindings.get(source) else {
            return Ok(None);
        };
        if let Some(expected) = &self.login {
            let Endpoint::Http { url, token_env } = &binding.endpoint else {
                return Err("managed profiles require their assigned HTTP endpoint".into());
            };
            if token_env != crate::login::local::TOKEN_ENV || url != &expected.state_url {
                return Err("managed profile binding differs from the assigned endpoint".into());
            }
            return expected.bound(&self.home, &self.name).map(Some);
        }
        let Some(name) = binding.credential() else {
            return Ok(None);
        };
        if name == crate::login::local::TOKEN_ENV
            && crate::login::local::state(&self.home)?.is_some()
        {
            return Err(
                "the login credential can only be used by the host's assigned profiles".into(),
            );
        }
        std::env::var(name)
            .ok()
            .filter(|s| !s.is_empty())
            .map(Some)
            .ok_or_else(|| format!("required credential variable {name} is unset"))
    }

    pub fn machine_credential(&self) -> Option<crate::login::local::MachineCredential> {
        self.login
            .as_ref()
            .map(|s| crate::login::local::MachineCredential {
                home: self.home.clone(),
                host: s.host.clone(),
                account: s.account.id.clone(),
                state_url: s.state_url.clone(),
                profile: self.name.clone(),
                workspace: self.profile.workspace_id.clone(),
            })
    }

    pub fn file_credential(&self, name: &str) -> bool {
        name == crate::login::local::TOKEN_ENV
            && self
                .login
                .as_ref()
                .is_some_and(|s| s.credential_source == crate::login::local::Source::File)
    }

    pub fn report(&self) -> Value {
        let mut required = Vec::new();
        let mut missing = Vec::new();
        let mut issue = self.conflicts(false).and_then(|()| self.check()).err();
        for source in &self.profile.sources {
            let Some(name) = self.bindings.get(source).and_then(|b| b.credential()) else {
                continue;
            };
            let needs_environment = !self.file_credential(name);
            if needs_environment {
                required.push(name);
            }
            if let Err(error) = self.credential(source) {
                if needs_environment {
                    missing.push(name);
                }
                if self.login.is_some() {
                    issue.get_or_insert(error);
                }
            }
        }
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
