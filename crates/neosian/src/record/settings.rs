//! The verb's settings (`record/settings.py`, `memory/settings.py`): the
//! mount half of the grammar with the verb's degrade rule, the layout a
//! session derives from, Cursor's workspace anchor. Nothing here builds
//! a store.

use std::env;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::cli::{HINT, Record};
use super::payload::Payload;
use crate::home::{project_mounts, user_mount};
use crate::memory::grammar::{SCOPE_ENV, SUGAR_MOUNT_PATH};
use crate::memory::mounts::{Mount, Refusal};

/// Everything the verb needs: the store, the mounts and the one the
/// sessions document lands in, the agent's kind, the spool.
pub struct Settings {
    pub root: PathBuf,
    pub mounts: Vec<Mount>,
    pub sessions_mount: usize,
    pub agent: String,
    pub spool: PathBuf,
}

/// The mount half of the grammar: the flags, else `NEOSIAN_SCOPE`, else
/// `layout`'s project layout, a nameless directory degrading to the user
/// mount alone. The outer error is the grammar's (exit 2), the inner the
/// environment's: a login the process does not name (exit 1).
pub fn resolve_mounts(args: &Record, layout: &Path) -> Result<Result<Vec<Mount>, String>, Refusal> {
    if args.scope.is_some() && !args.mount.is_empty() {
        let sugar = "--scope is the single-mount sugar; use --mount for multi-mount";
        return Err(Refusal::Usage(sugar.to_string()));
    }
    if !args.mount.is_empty() {
        let mounts: Vec<Mount> = args
            .mount
            .iter()
            .map(|token| Mount::parse(token))
            .collect::<Result<_, _>>()?;
        let twice = |mount: &Mount| {
            mounts
                .iter()
                .filter(|other| other.path == mount.path)
                .count()
                > 1
        };
        if mounts.iter().any(twice) {
            return Err(Refusal::Usage("mount paths must be unique".to_string()));
        }
        return Ok(Ok(mounts));
    }
    let named = env::var(SCOPE_ENV).ok().filter(|scope| !scope.is_empty());
    if let Some(scope) = args.scope.as_ref().or(named.as_ref()) {
        return Ok(Ok(vec![Mount::new(
            scope,
            SUGAR_MOUNT_PATH,
            (false, false),
            "",
        )?]));
    }
    Ok(match project_mounts(layout) {
        Ok(mounts) => Ok(mounts),
        Err(_) => match user_mount() {
            Ok(user) => Ok(vec![user]),
            Err(Refusal::Usage(message)) => Err(message),
            Err(refusal) => return Err(refusal),
        },
    })
}

/// `args.project or Path.cwd()`, an empty value the working directory.
pub fn layout_of(project: Option<&Path>, cwd: &Path) -> PathBuf {
    match project {
        Some(path) if !path.as_os_str().is_empty() => path.to_path_buf(),
        _ => cwd.to_path_buf(),
    }
}

/// The environment's refusal: exit 1, never 2, which would block the prompt.
pub fn environment(message: &str, err: &mut impl Write) -> io::Result<u8> {
    writeln!(err, "error: {message}\nhint: {HINT}")?;
    Ok(1)
}

/// Cursor's project is its workspace root when there is exactly one.
pub fn cursor_project(payload: &Payload) -> PathBuf {
    match payload.get("workspace_roots").and_then(Value::as_array) {
        Some(roots) if roots.len() == 1 => match roots[0].as_str() {
            Some(root) if Path::new(root).is_absolute() => PathBuf::from(root),
            _ => PathBuf::from("/"),
        },
        _ => PathBuf::from("/"),
    }
}
