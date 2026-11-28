use std::io::{BufRead, BufReader};

use serde_json::{Value, json};

use super::*;
use crate::harness::native::tests as fixtures;

fn fed(agent: Agent, lines: &[&str]) -> Capture {
    let mut capture = Capture::new(agent);
    for line in lines {
        capture.push(line.as_bytes());
        capture.push(b"\n");
    }
    capture
}

fn tools(captured: Captured) -> Vec<Value> {
    captured
        .tools
        .map(|file| {
            BufReader::new(file)
                .lines()
                .map(|line| serde_json::from_str(&line.expect("line")).expect("json"))
                .collect()
        })
        .unwrap_or_default()
}

#[test]
fn a_claude_code_turn_is_what_its_hooks_would_have_sent() {
    let captured = fed(Agent::ClaudeCode, fixtures::claude()).finish();
    assert_eq!(
        captured.session.as_deref(),
        Some("5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe")
    );
    assert!(captured.completed && !captured.over && captured.error.is_none());
    assert_eq!(captured.answer, "done");
    let spooled = tools(captured);
    assert_eq!(spooled.len(), 2);
    assert_eq!(
        spooled[1],
        json!({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "touch probe2.txt", "description": "Create an empty file"},
            "tool_use_id": "toolu_01Tp",
            "tool_response": {"stdout": "", "stderr": "", "interrupted": false, "isImage": false, "noOutputExpected": true},
        })
    );
    assert_eq!(spooled[0]["tool_name"], "Write");
}

#[test]
fn codex_and_opencode_answers_are_their_last_message_and_last_step() {
    let codex = fed(Agent::Codex, fixtures::codex()).finish();
    assert!(codex.completed);
    assert_eq!(codex.answer, "done");
    assert_eq!(tools(codex).len(), 2);
    let opencode = fed(Agent::OpenCode, fixtures::opencode()).finish();
    assert!(opencode.completed);
    assert_eq!(opencode.answer, "Succeeded: created `probe8.txt`.");
    let kept = tools(opencode).len();
    assert_eq!(kept, 2, "an error result is kept, as the plugin keeps it");
    let mut steps = Capture::new(Agent::OpenCode);
    for line in [
        r#"{"type":"step_start","sessionID":"ses_a","part":{}}"#,
        r#"{"type":"text","sessionID":"ses_a","part":{"text":"one"}}"#,
        r#"{"type":"text","sessionID":"ses_a","part":{"text":"two"}}"#,
        r#"{"type":"step_finish","sessionID":"ses_a","part":{"reason":"stop"}}"#,
    ] {
        steps.push(format!("{line}\n").as_bytes());
    }
    assert_eq!(steps.finish().answer, "one\ntwo");
}

#[test]
fn one_completed_turn_is_one_record_whatever_repeats() {
    let mut lines: Vec<&str> = fixtures::codex().to_vec();
    // The same tool completion twice, then the completion twice, then a
    // call after the turn completed.
    lines.insert(5, fixtures::codex()[4]);
    lines.push(r#"{"type":"turn.completed","usage":{}}"#);
    lines.push(r#"{"type":"item.completed","item":{"id":"item_9","type":"command_execution","command":"late","aggregated_output":"","exit_code":0}}"#);
    lines.push(r#"{"type":"turn.failed","error":{"message":"late"}}"#);
    let captured = fed(Agent::Codex, &lines).finish();
    assert!(captured.completed);
    let ids: Vec<Value> = tools(captured)
        .into_iter()
        .map(|tool| tool["tool_use_id"].clone())
        .collect();
    assert_eq!(ids, [json!("item_1"), json!("item_2")]);
}

#[test]
fn a_failure_before_the_completion_is_a_turn_not_completed() {
    let mut capture = Capture::new(Agent::Codex);
    capture.push(b"{\"type\":\"thread.started\",\"thread_id\":\"t-1\"}\n");
    capture.push(b"{\"type\":\"turn.failed\",\"error\":{\"message\":\"quota\"}}\n");
    capture.push(b"{\"type\":\"turn.completed\"}\n");
    let captured = capture.finish();
    assert!(!captured.completed);
    let silent = fed(Agent::ClaudeCode, &fixtures::claude()[..8]).finish();
    assert!(!silent.completed, "no result line, no completed turn");
}

#[test]
fn output_split_anywhere_reads_the_same() {
    let whole: String = fixtures::claude()
        .iter()
        .map(|line| format!("{line}\n"))
        .collect();
    let mut capture = Capture::new(Agent::ClaudeCode);
    for piece in whole.as_bytes().chunks(7) {
        capture.push(piece);
    }
    let captured = capture.finish();
    assert!(captured.completed);
    assert_eq!(tools(captured).len(), 2);
    // The last line needs no newline.
    let mut capture = Capture::new(Agent::Codex);
    capture.push(fixtures::codex().join("\n").as_bytes());
    assert!(capture.finish().completed);
}

#[test]
fn a_turn_past_its_bound_is_marked_and_keeps_nothing_more() {
    let big = format!(
        r#"{{"type":"item.completed","item":{{"id":"big","type":"command_execution","command":"x","aggregated_output":"{}","exit_code":0}}}}"#,
        "o".repeat(4096)
    );
    let mut lines: Vec<&str> = fixtures::codex().to_vec();
    lines.insert(2, &big);
    let mut wide = Capture::bounded(Agent::Codex, LINE_BYTES, 64 << 10);
    let mut narrow = Capture::bounded(Agent::Codex, LINE_BYTES, 2048);
    let mut short = Capture::bounded(Agent::Codex, 1024, TURN_BYTES);
    for line in &lines {
        for capture in [&mut wide, &mut narrow, &mut short] {
            capture.push(line.as_bytes());
            capture.push(b"\n");
        }
    }
    let wide = wide.finish();
    assert!(!wide.over && wide.completed);
    assert_eq!(tools(wide).len(), 3);
    let narrow = narrow.finish();
    assert!(narrow.over, "the turn passed 2 KiB");
    let short = short.finish();
    assert!(short.over, "a line passed 1 KiB");
    assert!(!short.completed, "nothing is read past the bound (X4e P5)");
    let mut said = Capture::bounded(Agent::OpenCode, LINE_BYTES, 100);
    let text = format!(
        r#"{{"type":"text","sessionID":"s","part":{{"text":"{}"}}}}"#,
        "w".repeat(101)
    );
    said.push(format!("{text}\n").as_bytes());
    assert!(said.finish().over, "the answer counts toward the bound");
}

#[test]
fn the_spool_has_no_name_in_the_directory() {
    use std::os::unix::fs::MetadataExt;
    let captured = fed(Agent::Codex, fixtures::codex()).finish();
    let file = captured.tools.expect("spooled");
    let metadata = file.metadata().expect("metadata");
    assert_eq!(metadata.nlink(), 0, "unlinked as soon as it was made");
    assert_eq!(metadata.mode() & 0o777, 0o600);
}

#[test]
fn retained_words_and_waiting_inputs_count_and_are_let_go_past_the_bound() {
    // X4e P5: OpenCode keeps a step's words; below the line bound each, they
    // still pass the turn's bound together, and the capture lets go.
    let mut said = Capture::bounded(Agent::OpenCode, 1024, 4096);
    let line = format!(
        r#"{{"type":"text","sessionID":"s","part":{{"text":"{}"}}}}"#,
        "w".repeat(900)
    );
    for _ in 0..6 {
        said.push(format!("{line}\n").as_bytes());
    }
    assert!(said.over && said.texts.is_empty() && said.text_bytes == 0);
    let kept = said.line.capacity();
    said.push(format!("{line}\n").as_bytes());
    assert!(said.texts.is_empty() && said.line.capacity() == kept);
    // Claude Code's calls waiting for their results count by their inputs.
    let mut waiting = Capture::bounded(Agent::ClaudeCode, LINE_BYTES, 4096);
    for n in 0..8 {
        let call = json!({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": format!("t{n}"), "name": "Read",
             "input": {"path": "p".repeat(900)}}
        ]}});
        waiting.push(format!("{call}\n").as_bytes());
    }
    assert!(waiting.over && waiting.calls.is_empty() && waiting.pending == 0);
    assert!(waiting.finish().over);
}
