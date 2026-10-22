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
    File(PathBuf),
    Remote { url: String, token: String },
}

impl Selection {
    /// Resolve the URL half without constructing a home or an HTTP client.
    /// The caller keeps the existing unsupported-Postgres diagnostic.
    pub fn resolve(root: Option<&Path>, url: Option<&str>) -> Result<Self, String> {
        let dsn = std::env::var_os(DSN_ENV).is_some_and(|dsn| !dsn.is_empty());
        let named: Vec<&str> = [
            ("--root", root.is_some()),
            (DSN_ENV, dsn),
            ("--url", url.is_some()),
        ]
        .into_iter()
        .filter_map(|(name, present)| present.then_some(name))
        .collect();
        if url.is_some() && named.len() > 1 {
            return Err(format!("{} are mutually exclusive", named.join(" and ")));
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
            Self::File(root) => Ok(Arc::new(FileStore::open(root)?)),
            Self::Remote { url, token } => Ok(Arc::new(RemoteStore::connect(url, token)?)),
        }
    }
}
