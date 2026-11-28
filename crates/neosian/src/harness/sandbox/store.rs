//! The parent's store and a child (X4 rulings, 2026-11-28; X4e P1). The
//! store is the one the parent resolved, passed on as explicit flags and
//! never resolved again from the child's directory; a selection that cannot
//! be resolved refuses the launch, since its store could not be protected.
//! No child writes the store: the watcher, outside the policy, records the
//! turn. No child holds the store's credential either: the credential
//! variables leave its environment and neosian's login state is unreadable
//! to it. A child of a store reached with a credential therefore has no
//! access of its own to that store, and is told so. A file store is held
//! read-only by the filesystem policy itself, with one exception its
//! readers need: neosian's file store takes its lock for a read by opening
//! the lock file for writing, so the policy allows data writes to that
//! existing file only (`policy.rs`); the file carries no content.

use std::path::{Path, PathBuf};

use super::super::text::say;
use crate::profile::files::Endpoint;
use crate::remote::selection::Selection;

/// The variables that carry a store's write credential, removed from every
/// child's environment.
pub const CREDENTIAL_VARS: [&str; 4] = [
    crate::remote::selection::TOKEN_ENV,
    crate::memory::cli::DSN_ENV,
    crate::login::local::TOKEN_ENV,
    // A serving token the parent also holds is a bearer for the same store
    // (X4f Q2).
    crate::serve::cli::TOKEN_ENV,
];

/// The lock a file store's reader takes, the one profile selection takes
/// in neosian's home once a login exists, and where the login state lives.
const STORE_LOCK: &str = ".store.lock";
const PROFILE_LOCK: &str = ".profile.lock";
const LOGIN: &str = "login";

/// What the policy needs from the parent's store selection.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Stores {
    /// Neosian's homes and every file store root: never writable.
    pub protected: Vec<PathBuf>,
    /// Lock files a reader opens for writing: data writes only.
    pub locks: Vec<PathBuf>,
    /// Neosian's login state in each home: never readable.
    pub hidden: Vec<PathBuf>,
    /// Variables that carry a credential of this store: never passed on.
    pub unset: Vec<String>,
    /// The store is reached with a credential, which the child never gets.
    pub credential: bool,
}

impl Stores {
    fn file(&mut self, root: PathBuf) {
        if !self.protected.contains(&root) {
            self.locks.push(root.join(STORE_LOCK));
            self.protected.push(root);
        }
    }
}

/// The parent's backend, from its typed flags alone (X4f Q1): exactly one
/// of a named profile, an absolute `--root`, a `--url` or a PostgreSQL
/// `--schema`, or none for neosian's home; never a profile the child's
/// directory is bound to, and never the store variables, which a typed
/// selection already names.
pub(super) fn parent(selection: &[String], directory: &Path) -> Result<Selection, String> {
    let flag = |name: &str| {
        selection
            .chunks(2)
            .find(|pair| pair.len() == 2 && pair[0] == name)
            .map(|pair| pair[1].as_str())
    };
    match (
        flag("--profile"),
        flag("--root"),
        flag("--url"),
        flag("--schema"),
    ) {
        (Some(name), None, None, None) => crate::profile::resolve::selected(Some(name), directory)?
            .map(|found| Selection::Profile(Box::new(found)))
            .ok_or_else(|| format!("profile {name} is not installed")),
        (None, Some(root), None, None) if Path::new(root).is_absolute() => {
            Ok(Selection::File(root.into()))
        }
        (None, None, Some(url), None) => Ok(Selection::Remote {
            url: url.to_string(),
            token: String::new(),
        }),
        (None, None, None, Some(schema)) => Ok(Selection::Postgres {
            dsn: String::new(),
            schema: schema.to_string(),
        }),
        // A plan that names none (the server always names one): the home.
        (None, None, None, None) => crate::home::home()
            .map(Selection::File)
            .map_err(|error| error.to_string()),
        _ => Err("the server's selection does not name exactly one store".to_string()),
    }
}

/// The stores of the parent's selection, or why they cannot be protected.
pub fn check(selection: &[String], directory: &Path) -> Result<Stores, String> {
    let selected = parent(selection, directory)
        .map_err(|error| say("sandbox_store_unknown", &[("error", &error)]))?;
    let mut stores = Stores {
        unset: CREDENTIAL_VARS.map(String::from).to_vec(),
        ..Stores::default()
    };
    // The server's home, and the default one a harness's own registration
    // reaches when it drops NEOSIAN_HOME, as Codex does.
    let default = std::env::home_dir().map(|home| home.join(".neosian"));
    for home in crate::home::home().ok().into_iter().chain(default) {
        if !stores.protected.contains(&home) {
            stores.locks.push(home.join(PROFILE_LOCK));
            stores.hidden.push(home.join(LOGIN));
            stores.file(home);
        }
    }
    match selected {
        Selection::File(root) => stores.file(root),
        Selection::Profile(profile) => {
            for binding in profile.bindings.values() {
                if let Endpoint::File { root } = &binding.endpoint {
                    stores.file(root.clone());
                }
                if let Some(name) = binding.credential() {
                    stores.credential = true;
                    if !stores.unset.iter().any(|known| known == name) {
                        stores.unset.push(name.to_string());
                    }
                }
            }
            stores.credential |= profile.login.is_some();
        }
        Selection::Remote { .. } | Selection::Postgres { .. } => stores.credential = true,
    }
    Ok(stores)
}

/// The lock of each file store that exists made to exist too before the
/// child starts, with the store's own lock (taken and released at once), so
/// the child's reader never needs to create it. The profile lock is left as
/// it is.
pub fn ready(stores: &Stores) {
    for lock in &stores.locks {
        let store = lock.parent().is_some_and(Path::is_dir);
        if store && lock.ends_with(STORE_LOCK) && lock.symlink_metadata().is_err() {
            let _ = crate::store::lock::acquire(lock);
        }
    }
}
