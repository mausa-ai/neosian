//! The tool's prose (`assets/prompts/harness.yaml`): its description, its
//! parameters and every sentence it answers with, rendered here.

use crate::assets::{block, mapping, quoted, render};

pub const HARNESS: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/prompts/harness.yaml"));

pub fn description() -> String {
    block(HARNESS, "tool").expect("harness.yaml carries the tool")
}

pub fn params() -> Vec<(String, String)> {
    mapping(HARNESS, "params").expect("harness.yaml carries the params")
}

/// The sentence `key`, its `{{variables}}` filled.
pub fn say(key: &str, variables: &[(&str, &str)]) -> String {
    let template = quoted(HARNESS, key).unwrap_or_else(|| panic!("harness.yaml carries {key}"));
    render(&template, variables)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_sentence_the_tool_says_is_in_the_asset() {
        for key in [
            "running",
            "ended",
            "stopped_note",
            "recorded",
            "not_recorded",
            "record_unknown",
            "record_failed",
            "record_pending",
            "record_off",
            "record_paused",
            "record_excluded",
            "record_gap",
            "record_bound",
            "record_session",
            "record_error",
            "record_elsewhere",
            "record_slow",
            "record_spool_unreadable",
            "linked",
            "link_written",
            "not_linked_session",
            "not_linked_absent",
            "not_linked_agent",
            "not_linked_mount",
            "not_linked_retry",
            "link_failed",
            "announced_existing",
            "output_cut",
            "answer_cut",
            "timed_out",
            "child_refused",
            "missing_harness",
            "none_available",
            "too_many",
            "busy",
            "stopped",
            "unknown_child",
            "no_session",
            "required",
            "bad_directory",
            "not_allowed",
            "need_directory",
            "bad_model",
            "bad_prompt",
            "bad_session",
            "bad_seconds",
            "bad_opencode_config",
            "spawn_failed",
            "codex_probe_failed",
            "codex_list_failed",
            "codex_unswitchable",
            "codex_unnamed",
            "opencode_agent",
            "watcher_lost",
            "watch_off",
            "watch_bad_field",
            "watch_missing",
            "config_unreadable",
            "config_table",
            "config_enabled",
            "config_bounded",
            "config_directories",
            "config_unknown",
        ] {
            let sentence = say(key, &[]);
            assert!(
                !sentence.is_empty() && !sentence.contains('\u{2014}'),
                "{key}"
            );
        }
        assert!(description().contains("read-only"));
        let names: Vec<String> = params().into_iter().map(|(name, _)| name).collect();
        assert_eq!(
            names,
            [
                "command",
                "agent",
                "prompt",
                "mode",
                "model",
                "directory",
                "child",
                "session",
                "seconds"
            ]
        );
        assert_eq!(
            say("busy", &[("child", "h1")]),
            "Child h1 is still running a turn; wait for it or stop it first."
        );
    }
}
