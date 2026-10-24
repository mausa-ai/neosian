//! Pure store selection, followed by construction only after argv passes.

use super::RemoteStore;
use crate::home::home;
use crate::memory::cli::DSN_ENV;
use crate::repr::repr;
use crate::state::State;
use crate::store::FileStore;
use crate::store::error::Error;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub const TOKEN_ENV: &str = "NEOSIAN_CLIENT_TOKEN";

pub enum Selection {
    Profile(Box<crate::profile::resolve::Resolved>),
    File(PathBuf),
    Postgres { dsn: String, schema: String },
    Remote { url: String, token: String },
}

impl Selection {
    pub fn resolve_profile(
        root: Option<&Path>,
        url: Option<&str>,
        schema: Option<&str>,
        profile: Option<&str>,
        project: &Path,
        mounts: bool,
    ) -> Result<Self, String> {
        if let Some(selected) = crate::profile::resolve::selected(profile, project)? {
            selected.conflicts(root.is_some() || url.is_some() || schema.is_some() || mounts)?;
            selected.check()?;
            return Ok(Self::Profile(Box::new(selected)));
        }
        Self::resolve_schema(root, url, schema)
    }

    pub fn mounts(&self) -> Option<Result<Vec<crate::memory::mounts::Mount>, String>> {
        let Self::Profile(p) = self else {
            return None;
        };
        Some(
            p.profile
                .mounts
                .iter()
                .map(|m| {
                    use crate::profile::model::Access;
                    crate::memory::mounts::Mount::new(
                        &m.scope,
                        &m.path,
                        (m.access == Access::Ro, m.access == Access::Eo),
                        "",
                    )
                    .map_err(|_| "invalid profile mount".into())
                })
                .collect(),
        )
    }
    /// Resolve store selection without constructing a home or a database client.
    pub fn resolve_schema(
        root: Option<&Path>,
        url: Option<&str>,
        schema: Option<&str>,
    ) -> Result<Self, String> {
        let dsn = std::env::var_os(DSN_ENV).is_some_and(|dsn| !dsn.is_empty());
        let named: Vec<&str> = [
            ("--root", root.is_some()),
            (DSN_ENV, dsn),
            ("--url", url.is_some()),
        ]
        .into_iter()
        .filter_map(|(name, present)| present.then_some(name))
        .collect();
        if named.len() > 1 {
            return Err(format!("{} are mutually exclusive", named.join(" and ")));
        }
        if schema.is_some() && !dsn {
            return Err("--schema applies only to Postgres (unset --root/--url)".to_string());
        }
        if dsn {
            return Ok(Self::Postgres {
                dsn: std::env::var(DSN_ENV)
                    .map_err(|_| "Postgres DSN must be UTF-8".to_string())?,
                schema: schema.unwrap_or("neosian").to_string(),
            });
        }
        if let Some(url) = url {
            if !url.starts_with("http://") && !url.starts_with("https://") {
                return Err(format!(
                    "--url must start with http:// or https://, got {}",
                    repr(url)
                ));
            }
            let token = std::env::var(TOKEN_ENV).ok().filter(|token| !token.is_empty()).ok_or_else(|| {
                format!("--url needs {TOKEN_ENV} — the client's bearer token, never on the command line")
            })?;
            return Ok(Self::Remote {
                url: url.to_string(),
                token,
            });
        }
        let root = match root {
            Some(root) => root.to_path_buf(),
            None => home().map_err(|e| e.to_string())?,
        };
        Ok(Self::File(root))
    }

    pub fn open(&self) -> Result<Arc<dyn State>, Error> {
        match self {
            Self::Profile(profile) => profile.open(),
            Self::Postgres { dsn, schema } => {
                Ok(Arc::new(crate::postgres::PostgresStore::open(dsn, schema)?))
            }
            Self::File(root) => Ok(Arc::new(FileStore::open(root)?)),
            Self::Remote { url, token } => Ok(Arc::new(RemoteStore::connect(url, token)?)),
        }
    }
}
