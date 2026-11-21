//! `[harness]` in neosian's own `config.toml` (§40.50 item 1): off unless
//! `enabled = true`; a value of the wrong type or out of range keeps the
//! tool off and says why, so a typo never widens what the server does.

use std::path::Path;

const TURN_SECONDS: (i64, i64, i64) = (1800, 10, 86_400);
const MAX_CHILDREN: (i64, i64, i64) = (2, 1, 8);

/// What a server that serves the tool may do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    /// The longest a turn may run before it is stopped.
    pub turn_seconds: u64,
    /// How many children that are not stopped may exist at once.
    pub max_children: usize,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            turn_seconds: TURN_SECONDS.0 as u64,
            max_children: MAX_CHILDREN.0 as usize,
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
        Some(_) => Err(format!(
            "harness tool off: [harness] {key} must be an integer from {low} to {high}"
        )),
    }
}

/// The settings when enabled, None when off, Err when `[harness]` exists
/// but cannot be trusted.
pub fn load(path: &Path) -> Result<Option<Settings>, String> {
    let document = match crate::config::read(path) {
        Ok(document) => document,
        Err(error) => return Err(format!("harness tool off: {error}")),
    };
    let Some(section) = document.get("harness") else {
        return Ok(None);
    };
    let Some(table) = section.as_table() else {
        return Err("harness tool off: [harness] must be a table".to_string());
    };
    let enabled = match table.get("enabled") {
        None => false,
        Some(toml::Value::Boolean(enabled)) => *enabled,
        Some(_) => {
            return Err("harness tool off: [harness] enabled must be true or false".to_string());
        }
    };
    let turn_seconds = bounded(table, "turn_seconds", TURN_SECONDS)?;
    let max_children = bounded(table, "max_children", MAX_CHILDREN)?;
    if let Some(key) = table
        .keys()
        .find(|key| !["enabled", "turn_seconds", "max_children"].contains(&key.as_str()))
    {
        return Err(format!(
            "harness tool off: [harness] has an unknown key {key}"
        ));
    }
    Ok(enabled.then_some(Settings {
        turn_seconds: turn_seconds as u64,
        max_children: max_children as usize,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn loaded(text: &str) -> Result<Option<Settings>, String> {
        let dir = crate::testing::Scratch::new("harness-config");
        dir.plant("config.toml", text);
        load(&dir.path().join("config.toml"))
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
                max_children: 8
            }))
        );
        for bad in [
            "harness = true\n",
            "[harness]\nenabled = 'yes'\n",
            "[harness]\nenabled = true\nturn_seconds = 9\n",
            "[harness]\nenabled = true\nmax_children = 0\n",
            "[harness]\nenabled = true\nmax_children = 2.5\n",
            "[harness]\nenabled = true\nmax_child = 3\n",
            "[harness\n",
        ] {
            assert!(
                loaded(bad).unwrap_err().starts_with("harness tool off: "),
                "{bad}"
            );
        }
    }
}
