//! What a lander can serve (DESIGN §40.54 item 17.3; rulings D2 and E3
//! of the T1e1 plan, and the T1e1c note on direct URLs): a queued turn's
//! destination rebuilt from this process's own environment, which a hook
//! started it with (D13). A direct turn needs the same root, the same DSN
//! and schema, or the same URL and token, the URL handed over by `--url`
//! when the stored one lost its userinfo or query; a profile turn's
//! profile is resolved from its name and project in the same home, and
//! serves while its capture is on.

use std::fs;
use std::path::{Path, PathBuf};

use super::super::fingerprint::fingerprint;
use super::cap::Cap;
use super::state::{Destination, State, describe, public_url};
use super::turn::Here;
use crate::memory::cli::DSN_ENV;
use crate::profile::model::CaptureMode;
use crate::remote::selection::{Selection, TOKEN_ENV};

/// What this process can land for one destination: the `Here` a Stop
/// builds from its own settings, built here from a queued turn's state.
pub struct Serving {
    selection: Selection,
    destination: Destination,
    fingerprint: Option<String>,
    scope: String,
    mount: String,
}

impl Serving {
    pub fn here<'a>(&'a self, cap: &'a Cap) -> Here<'a> {
        Here {
            selection: &self.selection,
            destination: self.destination.clone(),
            fingerprint: self.fingerprint.as_deref(),
            scope: &self.scope,
            mount: &self.mount,
            provenance: None,
            cap,
        }
    }
}

/// The destination of a queued turn as this process can serve it, or
/// `None` when its environment does not name it (ruling D2). `url` is the
/// URL a hook handed over, used when the stored URL is its public form.
pub fn serving(
    destination: &Destination,
    state: &State,
    home: &Path,
    url: Option<&str>,
) -> Option<Serving> {
    let variable = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
    let (selection, fingerprint, scope, mount) = match destination {
        Destination::File { root } => (Selection::File(PathBuf::from(root)), None, None, None),
        Destination::Postgres { schema, .. } => {
            let dsn = variable(DSN_ENV)?;
            let schema = schema.clone();
            (Selection::Postgres { dsn, schema }, None, None, None)
        }
        Destination::Remote { url: stored, .. } => {
            let handed = url.filter(|url| public_url(url) == *stored);
            let url = handed.map_or_else(|| stored.clone(), str::to_string);
            let token = variable(TOKEN_ENV)?;
            (Selection::Remote { url, token }, None, None, None)
        }
        Destination::Profile { name, project, .. } => {
            let resolved = crate::profile::resolve::selected(Some(name), Path::new(project));
            let resolved = resolved.ok()??;
            let same_home =
                fs::canonicalize(&resolved.home).ok()? == fs::canonicalize(home).ok()?;
            let off = resolved.profile.capture.mode == CaptureMode::Off
                || (resolved.login.is_some()
                    && crate::login::local::capture_off(&resolved.home).unwrap_or(true));
            if !same_home || off {
                return None;
            }
            let current = fingerprint(&resolved).ok()?;
            let scope = resolved.profile.history.as_ref().map(|h| h.scope.clone());
            let mount = Some("history".to_string());
            (
                Selection::Profile(Box::new(resolved)),
                Some(current),
                scope,
                mount,
            )
        }
    };
    if describe(&selection) != *destination {
        return None;
    }
    Some(Serving {
        selection,
        destination: destination.clone(),
        fingerprint,
        scope: scope.or_else(|| state.scope.clone()).unwrap_or_default(),
        mount: mount.or_else(|| state.mount.clone()).unwrap_or_default(),
    })
}
