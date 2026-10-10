//! The home (`memory/home.py`, DESIGN §22): one place for every project
//! and agent. `~/.neosian`, or `$NEOSIAN_HOME`, is the store when no flag
//! names one; a project is a scope in it, never a root of its own. Pure:
//! nothing here creates a directory.

use std::env;
use std::io;
use std::path::{Path, PathBuf};

use crate::memory::mounts::{Mount, Refusal};
use crate::repr::repr;

const HOME_ENV: &str = "NEOSIAN_HOME";
const FIX: &str = "pass --scope or --mount (or memory_scope=) to name the scope yourself";
const USER_PROSE: &str =
    "durable facts about the user: preferences, identity, standing constraints";
const PROJECT_PROSE: &str = "facts local to this project: decisions, layout, conventions";

/// `$NEOSIAN_HOME` (an empty value is unset, a leading `~` expands), else
/// `~/.neosian`.
pub fn home() -> io::Result<PathBuf> {
    let user =
        || env::home_dir().ok_or_else(|| io::Error::other("no home directory; set NEOSIAN_HOME"));
    match env::var_os(HOME_ENV).filter(|named| !named.is_empty()) {
        None => Ok(user()?.join(".neosian")),
        Some(named) => match Path::new(&named).strip_prefix("~") {
            Ok(below) => Ok(user()?.join(below)),
            Err(_) => Ok(named.into()),
        },
    }
}

/// A scope id from a name: the grammar's characters, the rest `-`.
fn slug(name: &str) -> String {
    let kept = |c: char| c.is_ascii_alphanumeric() || "_.-".contains(c);
    let slug: String = name
        .chars()
        .take(128)
        .map(|c| if kept(c) { c } else { '-' })
        .collect();
    slug.trim_matches(['-', '.']).to_string()
}

/// The login as `getpass.getuser` reads it from the environment. (The
/// Python door falls back to the password database; a process without
/// any of the four names is refused here, and names `--scope` as the fix.)
fn login() -> Result<String, Refusal> {
    let named = ["LOGNAME", "USER", "LNAME", "USERNAME"]
        .iter()
        .find_map(|key| env::var(key).ok().filter(|name| !name.is_empty()));
    match named
        .map(|name| slug(&name))
        .filter(|login| !login.is_empty())
    {
        Some(login) => Ok(login),
        None => Err(Refusal::Usage(format!(
            "the login cannot be read as a scope id; {FIX}"
        ))),
    }
}

/// The canonical two-mount layout for a working directory: the user's
/// durable facts at `/user`, the project's at `/project`, both read-write.
/// The slug is the directory's own name, so two same-named directories
/// share a scope by design; a directory without a name derives nothing.
pub fn project_mounts(cwd: &Path) -> Result<Vec<Mount>, Refusal> {
    let user = format!("user:{}", login()?);
    let user_mount = Mount::new(&user, "user", (false, false), USER_PROSE)?;
    let directory = cwd.canonicalize().unwrap_or_else(|_| cwd.to_path_buf());
    let name = directory.file_name().map(|name| name.to_string_lossy());
    let Some(project) = name.map(|name| slug(&name)).filter(|slug| !slug.is_empty()) else {
        let shown = repr(&directory.to_string_lossy());
        return Err(Refusal::Usage(format!(
            "no project name in {shown} to derive a scope from; {FIX}"
        )));
    };
    let scope = format!("{user}/proj:{project}");
    Ok(vec![
        user_mount,
        Mount::new(&scope, "project", (false, false), PROJECT_PROSE)?,
    ])
}

#[cfg(test)]
mod tests {
    use super::slug;

    #[test]
    fn a_slug_keeps_the_grammar_and_trims_the_edges() {
        for (name, id) in [
            ("neosian", "neosian"),
            ("My Project", "My-Project"),
            (".hidden.", "hidden"),
            ("café", "caf"),
            ("a_b.c-d", "a_b.c-d"),
            ("...", ""),
            ("日本", ""),
        ] {
            assert_eq!(slug(name), id, "{name}");
        }
        assert_eq!(slug(&"x".repeat(200)).len(), 128);
    }
}
