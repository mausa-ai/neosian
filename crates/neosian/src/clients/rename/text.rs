//! The rename's sentences, from `assets/prompts/rename.yaml` (prose is data).

use crate::assets::{quoted, render};

const RENAME: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/prompts/rename.yaml"));

/// A sentence by key; a missing key is a build defect a unit test catches.
pub fn get(key: &str) -> String {
    quoted(RENAME, key).unwrap_or_else(|| panic!("rename.yaml carries {key}"))
}

/// A sentence with its `{{name}}` placeholders filled.
pub fn fill(key: &str, variables: &[(&str, &str)]) -> String {
    render(&get(key), variables)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_key_the_code_names_is_in_the_asset() {
        let source = [
            include_str!("mod.rs"),
            include_str!("claude_cli.rs"),
            include_str!("moves.rs"),
            include_str!("report.rs"),
            include_str!("undo.rs"),
            include_str!("backup.rs"),
            include_str!("../write_key.rs"),
            include_str!("../refresh.rs"),
            include_str!("../status.rs"),
            include_str!("../setup.rs"),
        ]
        .concat();
        let pattern = fancy_regex::Regex::new(r#"text::(?:get|fill)\("([a-z_]+)""#).unwrap();
        for found in pattern.captures_iter(&source) {
            let key = found
                .unwrap()
                .get(1)
                .map(|m| m.as_str().to_owned())
                .unwrap();
            assert!(quoted(RENAME, &key).is_some(), "rename.yaml lacks {key}");
        }
        assert!(!RENAME.contains('\u{2014}'), "no em dashes in shipped copy");
    }
}
