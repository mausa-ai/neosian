//! The parent's store as the parent resolved it (X4f Q1). The watcher, its
//! recorder and its policy receive a typed selection: one backend named
//! explicitly (`--profile`, `--root` with an absolute path, `--url`, or
//! `--schema` for the PostgreSQL store the server's variable selects), an
//! absolute neosian home, and, for a profile, its destinations as the
//! server resolved them when it started. A profile rebound since, while the
//! server stayed open, refuses the launch, and its turn is not filed; the
//! capture consent of the moment still governs, as the recorder reads it.

use std::path::{Path, PathBuf};

use super::text::say;
use crate::remote::selection::Selection;

/// The parent's selection as explicit flags and, for a profile, the
/// identity of its destinations.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Pinned {
    pub flags: Vec<String>,
    pub identity: Option<String>,
}

/// The selection the server resolved, made explicit: never empty, never two
/// backends, so nothing resolves it again from a child's directory or meets
/// a store variable with a conflicting `--root`.
pub fn pin(selected: &Selection) -> Pinned {
    let word = |flag: &str, value: String| vec![flag.to_string(), value];
    match selected {
        Selection::Profile(profile) => Pinned {
            flags: word("--profile", profile.name.clone()),
            identity: Some(identity(profile)),
        },
        Selection::File(root) => Pinned {
            flags: word("--root", absolute(root).display().to_string()),
            identity: None,
        },
        Selection::Remote { url, .. } => Pinned {
            flags: word("--url", url.clone()),
            identity: None,
        },
        Selection::Postgres { schema, .. } => Pinned {
            flags: word("--schema", schema.clone()),
            identity: None,
        },
    }
}

fn absolute(path: &Path) -> PathBuf {
    std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Where a profile's turns and memory go: its workspace and each source's
/// endpoint, without the capture policy, which may change at any time.
pub fn identity(profile: &crate::profile::resolve::Resolved) -> String {
    let endpoints: std::collections::BTreeMap<&String, _> = profile
        .bindings
        .iter()
        .map(|(source, binding)| (source, &binding.endpoint))
        .collect();
    serde_json::json!({
        "profile": profile.name,
        "workspace": profile.profile.workspace_id,
        "endpoints": endpoints,
    })
    .to_string()
}

/// The server's neosian home as an absolute path, which the watcher and
/// its recorder are started with whatever directory they run in.
pub fn home() -> Option<PathBuf> {
    crate::home::home().ok().map(|home| absolute(&home))
}

/// Whether the parent's destination is still the one it resolved: a profile
/// pinned with its identity is read again by name and compared; one that
/// is gone has drifted too.
pub fn drifted(flags: &[String], identity: Option<&str>, directory: &Path) -> Result<(), String> {
    let named = flags
        .chunks(2)
        .find(|pair| pair.len() == 2 && pair[0] == "--profile")
        .map(|pair| pair[1].as_str());
    let (Some(name), Some(identity)) = (named, identity) else {
        return Ok(());
    };
    let now = crate::profile::resolve::selected(Some(name), directory)
        .ok()
        .flatten()
        .map(|profile| self::identity(&profile));
    if now.as_deref() == Some(identity) {
        return Ok(());
    }
    Err(say("store_drifted", &[("profile", name)]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_backend_is_named_once_and_postgres_gets_no_root() {
        let postgres = Selection::Postgres {
            dsn: "postgresql://x".to_string(),
            schema: "neosian".to_string(),
        };
        assert_eq!(pin(&postgres).flags, ["--schema", "neosian"]);
        let remote = Selection::Remote {
            url: "https://h".to_string(),
            token: "t".to_string(),
        };
        assert_eq!(pin(&remote).flags, ["--url", "https://h"]);
        let file = pin(&Selection::File(PathBuf::from("relative/home")));
        assert_eq!(file.flags[0], "--root");
        assert!(Path::new(&file.flags[1]).is_absolute());
        for pinned in [pin(&postgres), pin(&remote), file] {
            assert_eq!(pinned.flags.len(), 2);
            assert!(pinned.identity.is_none());
        }
    }

    #[test]
    fn a_selection_without_a_profile_never_drifts() {
        let flags = ["--root".to_string(), "/r".to_string()];
        assert_eq!(drifted(&flags, None, Path::new("/")), Ok(()));
    }
}
