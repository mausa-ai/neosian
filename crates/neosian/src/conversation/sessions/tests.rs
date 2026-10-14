//! The sessions vocabulary's tests: the baton and the document as
//! written and read back, the declarations read from a turn's calls.

use serde_json::json;

use super::super::message::from_json;
use super::*;

const BATON: &str = "# handoff from claude-code:s\n\n- from: claude-code:s\n- written: 2026-10-12T22:27:56.085324Z\n- status: pending\n- conversation: s\n\nPick up the release.\n";

#[test]
fn the_baton_reads_back_and_renders_as_written() {
    let baton = parse_handoff(BATON).unwrap();
    assert_eq!(
        (baton.actor.as_str(), baton.note.as_str()),
        ("claude-code:s", "Pick up the release.")
    );
    assert_eq!(baton.written.z(), "2026-10-12T22:27:56.085324Z");
    assert_eq!(
        (baton.conversation.as_deref(), baton.pending),
        (Some("s"), true)
    );
    assert_eq!(
        handoff_document(&baton.actor, baton.written, &baton.note, Some("s")),
        BATON
    );
    let picked = BATON.replace(
        "status: pending",
        "status: picked up 2026-10-12T23:00:00Z by mcp:codex",
    );
    assert!(!parse_handoff(&picked).unwrap().pending);
    let unlinked = BATON.replace("- conversation: s\n", "");
    assert!(parse_handoff(&unlinked).unwrap().conversation.is_none());
    assert!(parse_handoff("# hand edit\n\nno fields\n").is_none());
    assert!(parse_handoff(&BATON.replace(".085324Z", "")).is_none());
    let now = |text| Stamp::parse(text).unwrap();
    assert!(matches!(
        handoff_tier(&baton, now("2026-10-19T22:27:00Z")),
        Some(Tier::Full)
    ));
    assert!(matches!(
        handoff_tier(&baton, now("2026-10-19T22:28:00Z")),
        Some(Tier::Line)
    ));
    assert!(
        handoff_tier(
            &parse_handoff(&picked).unwrap(),
            now("2026-10-13T00:00:00Z")
        )
        .is_none()
    );
}

#[test]
fn a_sessions_document_names_what_it_continues() {
    let document = "# codex session s\n\n- agent: codex\n- conversation: s\n- turns: 3\n- continues: a\n- continues: b\n";
    assert_eq!(continues(document), ["a", "b"]);
    assert!(continues("# nothing\n").is_empty());
    assert_eq!(sessions_path("s"), "sessions/s");
    let started = Stamp::parse("2026-10-13T21:56:33.167382Z").unwrap();
    let long = "x".repeat(250);
    let rendered = sessions_document(
        "claude-code",
        "s",
        started,
        Some(&format!("  {long}\nmore")),
        3,
        &["a".to_string()],
    );
    assert_eq!(
        rendered,
        format!(
            "# claude-code session s\n\n- agent: claude-code\n- conversation: s\n- started: 2026-10-13T21:56:33.167382Z\n- last prompt: {}\n- turns: 3\n- continues: a\n",
            "x".repeat(200)
        )
    );
    assert!(sessions_document("codex", "s", started, None, 1, &[]).contains("- last prompt: -\n"));
    assert!(
        sessions_document("codex", "s", started, Some(" \n"), 1, &[])
            .contains("- last prompt: -\n")
    );
}

#[test]
fn the_declarations_are_read_from_the_calls_and_their_results() {
    let turn = |name: &str, result: &str| {
        vec![
            from_json(&json!({"role": "assistant", "content": null, "tool_calls": [{"id": "t1", "name": name, "arguments": {}}]})).unwrap(),
            from_json(&json!({"role": "tool", "content": result, "tool_call_id": "t1"})).unwrap(),
        ]
    };
    let header = "[continuing conversation abc-1 — written by codex:abc-1, 3 turns]";
    assert_eq!(
        continued_ids(&turn("mcp__neosian-memory__continue_session", header)),
        ["abc-1"]
    );
    assert_eq!(
        continued_ids(&turn(
            "continue_session",
            &format!("{{\"text\": \"{header}\"}}")
        )),
        ["abc-1"]
    );
    assert!(continued_ids(&turn("recall_turn", header)).is_empty());
    assert!(continued_ids(&turn("continue_session", "[continuing conversation ")).is_empty());
    assert!(continued_ids(&turn("continue_session", "nothing")).is_empty());
    assert!(handoff_declared(&turn(
        "handoff",
        "[handoff note recorded — the next session sees it]"
    )));
    assert!(handoff_declared(&turn(
        "mcp__neosian-memory__handoff",
        "ok [handoff note recorded"
    )));
    assert!(!handoff_declared(&turn("handoff", "refused")));
    assert!(!handoff_declared(&turn(
        "other_tool",
        "[handoff note recorded"
    )));
    let mounts = [
        Mount::new("user:me", "user", (true, false), "").unwrap(),
        Mount::new("user:me/proj:x", "project", (false, false), "").unwrap(),
    ];
    assert_eq!(sessions_mount(&mounts), Some(1));
    assert_eq!(sessions_mount(&mounts[..1]), None);
    let single = [Mount::new("user:me", "memories", (false, false), "").unwrap()];
    assert_eq!(sessions_mount(&single), Some(0));
}
