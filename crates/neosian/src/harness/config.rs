//! `[harness]` in neosian's own `config.toml` (§40.50 item 1, amended by
//! #430): off unless `enabled = true`; `directories` lists, exactly, the
//! directories other than the server's own a child may work in. A value of
//! the wrong type or out of range keeps the tool off and says why, so a typo
//! never widens what the server does.

use std::path::{Path, PathBuf};

use super::text::say;

const TURN_SECONDS: (i64, i64, i64) = (1800, 10, 86_400);
const MAX_CHILDREN: (i64, i64, i64) = (2, 1, 8);
const KEYS: [&str; 4] = ["enabled", "turn_seconds", "max_children", "directories"];

/// What a server that serves the tool may do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    /// The longest a turn may run before it is stopped.
    pub turn_seconds: u64,
    /// How many children that are not stopped may exist at once.
    pub max_children: usize,
    /// The listed directories, canonical when they exist.
    pub directories: Vec<PathBuf>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            turn_seconds: TURN_SECONDS.0 as u64,
            max_children: MAX_CHILDREN.0 as usize,
            directories: Vec::new(),
        }
    }
}

fn bounded(
    table: &toml::Table,
    key: &str,
    (default, low, high): (i64, i64, i64),
) -> Result<i64, String> {
    match table.get(key) {
        None => Ok(default),
        Some(toml::Value::Integer(value)) if (low..=high).contains(value) => Ok(*value),
        Some(_) => Err(say(
            "config_bounded",
            &[
                ("key", key),
                ("low", &low.to_string()),
                ("high", &high.to_string()),
            ],
        )),
    }
}

fn directories(table: &toml::Table) -> Result<Vec<PathBuf>, String> {
    let Some(value) = table.get("directories") else {
        return Ok(Vec::new());
    };
    let refused = || say("config_directories", &[]);
    let listed = value.as_array().ok_or_else(refused)?;
    listed
        .iter()
        .map(|entry| {
            let path = Path::new(entry.as_str().ok_or_else(refused)?);
            if !path.is_absolute() {
                return Err(refused());
            }
            Ok(path.canonicalize().unwrap_or_else(|_| path.to_path_buf()))
        })
        .collect()
}

/// The settings when enabled, None when off, Err when `[harness]` exists
/// but cannot be trusted.
pub fn load(path: &Path) -> Result<Option<Settings>, String> {
    let document = match crate::config::read(path) {
        Ok(document) => document,
        Err(error) => return Err(say("config_unreadable", &[("error", &error.to_string())])),
    };
    let Some(section) = document.get("harness") else {
        return Ok(None);
    };
    let Some(table) = section.as_table() else {
        return Err(say("config_table", &[]));
    };
    let enabled = match table.get("enabled") {
        None => false,
        Some(toml::Value::Boolean(enabled)) => *enabled,
        Some(_) => return Err(say("config_enabled", &[])),
    };
    let turn_seconds = bounded(table, "turn_seconds", TURN_SECONDS)?;
    let max_children = bounded(table, "max_children", MAX_CHILDREN)?;
    let directories = directories(table)?;
    if let Some(key) = table.keys().find(|key| !KEYS.contains(&key.as_str())) {
        return Err(say("config_unknown", &[("key", key)]));
    }
    Ok(enabled.then_some(Settings {
        turn_seconds: turn_seconds as u64,
        max_children: max_children as usize,
        directories,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn loaded(text: &str) -> Result<Option<Settings>, String> {
        let name = format!("harness-config-{}", uuid::Uuid::new_v4().simple());
        let dir = crate::testing::Scratch::new(&name);
        dir.plant("config.toml", text);
        load(&dir.path().join("config.toml"))
    }

    #[test]
    fn listed_directories_are_absolute_and_canonical() {
        let here = std::env::temp_dir().canonicalize().unwrap();
        let missing = std::env::temp_dir().join("no-such-dir-x2b");
        let text = format!(
            "[harness]\nenabled = true\ndirectories = [{:?}, {:?}]\n",
            std::env::temp_dir().display().to_string(),
            missing.display().to_string()
        );
        let settings = loaded(&text).unwrap().unwrap();
        assert_eq!(settings.directories, [here, missing]);
    }

    #[test]
    fn the_tool_is_off_unless_enabled_and_every_value_is_trusted() {
        assert_eq!(loaded(""), Ok(None));
        assert_eq!(loaded("[update]\nmode = 'off'\n"), Ok(None));
        assert_eq!(loaded("[harness]\n"), Ok(None));
        assert_eq!(loaded("[harness]\nenabled = false\n"), Ok(None));
        assert_eq!(
            loaded("[harness]\nenabled = true\n"),
            Ok(Some(Settings::default()))
        );
        assert_eq!(
            loaded("[harness]\nenabled = true\nturn_seconds = 10\nmax_children = 8\n"),
            Ok(Some(Settings {
                turn_seconds: 10,
                max_children: 8,
                directories: Vec::new(),
            }))
        );
        for bad in [
            "harness = true\n",
            "[harness]\nenabled = 'yes'\n",
            "[harness]\nenabled = true\nturn_seconds = 9\n",
            "[harness]\nenabled = true\nmax_children = 0\n",
            "[harness]\nenabled = true\nmax_children = 2.5\n",
            "[harness]\nenabled = true\nmax_child = 3\n",
            "[harness]\nenabled = true\ndirectories = 'x'\n",
            "[harness]\nenabled = true\ndirectories = ['relative/dir']\n",
            "[harness]\nenabled = true\ndirectories = [1]\n",
            "[harness\n",
        ] {
            assert!(
                loaded(bad).unwrap_err().starts_with("harness tool off: "),
                "{bad}"
            );
        }
    }
}
