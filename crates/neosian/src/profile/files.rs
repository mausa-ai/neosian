//! Local profile and binding files. Read-only inspection never creates a home.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use super::model::CaptureMode;
use super::{Profile, validate};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields, tag = "backend", rename_all = "lowercase")]
pub enum Endpoint {
    File { root: PathBuf },
    Http { url: String, token_env: String },
    Postgres { dsn_env: String, schema: String },
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub endpoint: Endpoint,
    #[serde(default)]
    pub capture: CaptureMode,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Project {
    pub root: PathBuf,
    pub profile: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Registry {
    pub binding_format: u32,
    pub installation_id: String,
    #[serde(default)]
    pub profiles: BTreeMap<String, BTreeMap<String, Binding>>,
    #[serde(default)]
    pub projects: Vec<Project>,
}

impl Default for Registry {
    fn default() -> Self {
        Self {
            binding_format: 1,
            installation_id: uuid::Uuid::new_v4().to_string(),
            profiles: BTreeMap::new(),
            projects: vec![],
        }
    }
}

pub fn home() -> Result<PathBuf, String> {
    crate::home::home().map_err(|_| "cannot resolve neosian home".into())
}

fn not_link(path: &Path) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_symlink() => {
            Err("profile configuration cannot be a symbolic link".into())
        }
        Ok(_) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err("cannot inspect profile configuration".into()),
    }
}

pub fn path(home: &Path, name: &str) -> Result<PathBuf, String> {
    validate::name(name)?;
    let dir = home.join("profiles");
    not_link(&dir)?;
    let path = dir.join(format!("{name}.toml"));
    not_link(&path)?;
    Ok(path)
}

pub fn read(home: &Path, name: &str) -> Result<Profile, String> {
    let text = fs::read_to_string(path(home, name)?)
        .map_err(|_| "cannot read selected profile".to_string())?;
    super::parse(&text)
}

pub fn registry(home: &Path) -> Result<Registry, String> {
    let path = home.join("profile-bindings.toml");
    not_link(&path)?;
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Registry::default()),
        Err(_) => return Err("cannot read profile bindings".into()),
    };
    if text.len() > 1_048_576 {
        return Err("profile bindings exceed 1 MiB".into());
    }
    let r: Registry =
        toml::from_str(&text).map_err(|_| "invalid profile bindings or unknown field")?;
    if r.binding_format != 1 {
        return Err("unsupported binding_format; expected 1".into());
    }
    validate::uuid(&r.installation_id)?;
    for (name, sources) in &r.profiles {
        validate::name(name)?;
        for (source, binding) in sources {
            validate::name(source)?;
            binding.validate()?;
        }
    }
    let mut roots = std::collections::HashSet::new();
    for project in &r.projects {
        validate::name(&project.profile)?;
        if !project.root.is_absolute() || !roots.insert(&project.root) {
            return Err("project bindings require unique absolute roots".into());
        }
    }
    Ok(r)
}

pub fn lock(home: &Path) -> Result<fs::File, String> {
    let path = home.join(".profile.lock");
    not_link(&path)?;
    crate::store::lock::acquire(&path).map_err(|_| "cannot lock profile configuration".into())
}

pub fn write_registry(home: &Path, r: &Registry) -> Result<(), String> {
    let text = toml::to_string_pretty(r).map_err(|_| "cannot encode profile bindings")?;
    let path = home.join("profile-bindings.toml");
    not_link(&path)?;
    crate::store::fileio::atomic_write(&path, &text, true)
        .map_err(|_| "cannot write profile bindings".into())
}

pub fn write(home: &Path, name: &str, profile: &Profile, replace: bool) -> Result<(), String> {
    let path = path(home, name)?;
    let text = super::encode(profile)?;
    if path.exists() && !replace {
        if fs::read_to_string(&path).ok().as_deref() == Some(&text) {
            return Ok(());
        }
        return Err("profile already exists; use --replace to overwrite it".into());
    }
    crate::store::fileio::private_mkdir(path.parent().expect("profile directory"))
        .map_err(|_| "cannot create profile directory")?;
    crate::store::fileio::atomic_write(&path, &text, true)
        .map_err(|_| "cannot write profile".into())
}

pub fn env_name(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
        || value.as_bytes()[0].is_ascii_digit()
    {
        return Err("credential references must be environment variable names".into());
    }
    Ok(())
}

impl Binding {
    pub fn credential(&self) -> Option<&str> {
        match &self.endpoint {
            Endpoint::Http { token_env, .. } => Some(token_env),
            Endpoint::Postgres { dsn_env, .. } => Some(dsn_env),
            Endpoint::File { .. } => None,
        }
    }
    pub fn validate(&self) -> Result<(), String> {
        match &self.endpoint {
            Endpoint::File { root } if !root.is_absolute() => {
                return Err("file bindings require an absolute root".into());
            }
            Endpoint::File { .. } => {}
            Endpoint::Http { url, token_env } => {
                env_name(token_env)?;
                let parsed: ureq::http::Uri = url.parse().map_err(|_| "invalid HTTP endpoint")?;
                if !matches!(parsed.scheme_str(), Some("http" | "https"))
                    || parsed.authority().is_none_or(|a| a.as_str().contains('@'))
                    || parsed.query().is_some()
                    || url.contains('#')
                {
                    return Err(
                        "HTTP endpoints must exclude credentials, query and fragment".into(),
                    );
                }
                if self.capture == CaptureMode::Local {
                    return Err("local capture requires a file binding".into());
                }
            }
            Endpoint::Postgres { dsn_env, schema } => {
                env_name(dsn_env)?;
                crate::postgres::validate_schema(schema).map_err(|_| "invalid Postgres schema")?;
                if self.capture == CaptureMode::Local {
                    return Err("local capture requires a file binding".into());
                }
            }
        }
        Ok(())
    }
}
