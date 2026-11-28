//! The designated store and a child (X4 rulings, 2026-11-28). No child
//! writes the store: the watcher, outside the policy, records the turn. A
//! read-only child's environment loses the store's write credential, and a
//! store reached with a credential refuses a read-only child, since a
//! credential the child could use would also write it over the open
//! network. A file store is held read-only by the filesystem policy itself,
//! with one exception its readers need: neosian's file store takes its lock
//! for a read by opening the lock file for writing, so the policy allows
//! data writes to that existing file only (no creating, removing or
//! replacing it); the file carries no content.

use std::path::{Path, PathBuf};

use super::super::adapter::Mode;
use super::super::text::say;
use crate::profile::files::Endpoint;
use crate::remote::selection::Selection;

/// The variables that carry a store's write credential, removed from a
/// read-only child's environment.
pub const CREDENTIAL_VARS: [&str; 3] = [
    crate::remote::selection::TOKEN_ENV,
    crate::memory::cli::DSN_ENV,
    crate::login::local::TOKEN_ENV,
];

/// The lock a file store's reader takes, and the one profile selection
/// takes in neosian's home once a login exists.
const STORE_LOCK: &str = ".store.lock";
const PROFILE_LOCK: &str = ".profile.lock";

/// What the policy needs from the store selection.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Stores {
    /// Neosian's home and every file store root: never writable.
    pub protected: Vec<PathBuf>,
    /// Lock files a reader opens for writing: data writes only.
    pub locks: Vec<PathBuf>,
}

impl Stores {
    fn file(&mut self, root: PathBuf) {
        self.locks.push(root.join(STORE_LOCK));
        self.protected.push(root);
    }
}

/// The stores of this selection, or why a read-only child cannot start.
pub fn check(mode: Mode, selection: &[String], directory: &Path) -> Result<Stores, String> {
    let flag = |name: &str| {
        selection
            .chunks(2)
            .find(|pair| pair[0] == name && pair.len() == 2)
            .map(|pair| pair[1].as_str())
    };
    let resolved = Selection::resolve_profile(
        flag("--root").map(Path::new),
        flag("--url"),
        flag("--schema"),
        flag("--profile"),
        directory,
        false,
    );
    let mut stores = Stores::default();
    // The server's home, and the default one a harness's own registration
    // reaches when it drops NEOSIAN_HOME, as Codex does.
    let default = std::env::home_dir().map(|home| home.join(".neosian"));
    for home in crate::home::home().ok().into_iter().chain(default) {
        if !stores.protected.contains(&home) {
            stores.locks.push(home.join(PROFILE_LOCK));
            stores.file(home);
        }
    }
    let reached = match resolved {
        Ok(Selection::File(root)) => {
            if !stores.protected.contains(&root) {
                stores.file(root);
            }
            None
        }
        Ok(Selection::Profile(profile)) => {
            for binding in profile.bindings.values() {
                if let Endpoint::File { root } = &binding.endpoint {
                    stores.file(root.clone());
                }
            }
            let credential = profile.login.is_some()
                || profile.bindings.values().any(|b| b.credential().is_some());
            credential.then(|| "a profile with a remote or database source".to_string())
        }
        Ok(Selection::Remote { .. }) => Some("an HTTP store".to_string()),
        Ok(Selection::Postgres { .. }) => Some("a Postgres store".to_string()),
        Err(error) if mode == Mode::ReadOnly => {
            return Err(say("sandbox_store_unknown", &[("error", &error)]));
        }
        Err(_) => None,
    };
    match (mode, reached) {
        (Mode::ReadOnly, Some(store)) => Err(say("sandbox_store", &[("store", &store)])),
        _ => Ok(stores),
    }
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
