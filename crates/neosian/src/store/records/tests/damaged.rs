//! Damaged or hostile files on a records store (an outside reading review of
//! the fix round, 2026-11-22): a state that cannot be read for any reason
//! blocks no other draft or restore; a turn number at its limit, read from
//! the log, is refused by name, never added to; a marker naming a draft
//! outside its conversation is refused; a records file whose record numbers
//! are not its lines' is refused by name.

use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use serde_json::json;

#[cfg(unix)]
use super::restore_source::{request, send, source, stream};
use super::{enabled, messages, part, turns};
use crate::conversation::Turn;
use crate::conversation::rows::render_turn;
use crate::conversation::rows_header::{RecordHeader, render_header};
#[cfg(unix)]
use crate::conversation::turn_records::Restore;
use crate::conversation::turn_records::{RecordRead, records_of};
use crate::store::time::Stamp;

fn small(text: &str) -> Vec<crate::conversation::turn_records::TurnRecord> {
    records_of(&messages(&[json!({"role": "user", "content": text})])).unwrap()
}

/// A file made unreadable; false where the test runs with the power to read
/// it anyway.
#[cfg(unix)]
fn unreadable(path: &std::path::Path) -> bool {
    fs::set_permissions(path, fs::Permissions::from_mode(0o000)).unwrap();
    fs::read(path).is_err()
}

#[cfg(unix)]
#[test]
fn a_state_that_cannot_be_read_for_any_reason_blocks_nothing() {
    let (_home, store) = enabled("records-damaged-permission");
    part(&store, "c-1", "locked", small("x"), false).unwrap();
    let drafts = store.conversation_dir("c-1").unwrap().join(".drafts");
    if !unreadable(&drafts.join("locked.state")) {
        return;
    }
    let opened = part(&store, "c-1", "fresh", small("y"), false);
    fs::set_permissions(
        drafts.join("locked.state"),
        fs::Permissions::from_mode(0o600),
    )
    .unwrap();
    assert_eq!(
        opened.unwrap().next_seq,
        1,
        "a draft beside an unreadable state opens"
    );
    let items = stream(&source(1_000));
    send(&store, request(&items[..4])).unwrap();
    let state = store.staging("c-1", &request(&[]).restore).unwrap().state;
    assert!(unreadable(&state));
    let other = Restore {
        restore: "other".into(),
        ..request(&items[..4])
    };
    let refused = send(&store, other)
        .map(|_| ())
        .map_err(|error| error.text());
    fs::set_permissions(&state, fs::Permissions::from_mode(0o600)).unwrap();
    let refused = refused.unwrap_err();
    assert!(
        refused.contains("restore_open"),
        "an unreadable restore counts as open by its time: {refused}"
    );
}

fn row(turn: u64) -> String {
    render_turn(&Turn {
        conversation_id: "c-1".into(),
        turn,
        messages: messages(&[json!({"role": "user", "content": "last"})]),
        created_at: Stamp::now(),
        actor: None,
        redacted: false,
    })
}

#[test]
fn a_turn_number_at_its_limit_is_refused_by_name() {
    let (home, store) = enabled("records-damaged-limit");
    home.plant("conversations/c-1/turns.jsonl", &row(u64::MAX));
    let appended = store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "x"})]),
            None,
        )
        .unwrap_err()
        .text();
    assert!(
        appended.contains("the last turn a conversation can hold"),
        "{appended}"
    );
    let sealed = part(&store, "c-1", "d", small("x"), true)
        .unwrap_err()
        .text();
    assert!(
        sealed.contains("the last turn a conversation can hold"),
        "{sealed}"
    );
    assert!(
        !store
            .conversation_dir("c-1")
            .unwrap()
            .join(".drafts/d.jsonl")
            .exists()
    );
    let header = RecordHeader {
        turn: 7,
        created_at: Stamp::now(),
        actor: None,
        records: 1,
        bytes: 1,
        draft: "d".into(),
    };
    home.plant("conversations/c-1/.drafts/sealing", &render_header(&header));
    let rolled = store.last_turn_number("c-1").unwrap_err().text();
    assert!(
        rolled.contains("a marked seal cannot be rolled forward"),
        "{rolled}"
    );
}

#[test]
fn a_marker_naming_a_draft_outside_its_conversation_is_refused() {
    let (home, store) = enabled("records-damaged-marker");
    part(&store, "c-2", "x", small("theirs"), false).unwrap();
    let theirs = store
        .conversation_dir("c-2")
        .unwrap()
        .join(".drafts/x.jsonl");
    let before = fs::read(&theirs).unwrap();
    let line = r#"{"neosian_format":2,"turn":1,"created_at":"2026-11-21T14:03:07.512000Z","actor":null,"records":1,"bytes":6,"draft":"../../c-2/.drafts/x"}"#;
    home.plant("conversations/c-1/.drafts/sealing", &format!("{line}\n"));
    let refused = store.read_turns("c-1", 0, None).unwrap_err().text();
    assert!(
        refused.contains("agent_conversation_format_unsupported"),
        "{refused}"
    );
    assert_eq!(fs::read(&theirs).unwrap(), before);
    assert!(turns(&store, "c-2").is_empty());
}

#[test]
fn a_records_file_whose_numbers_are_not_its_lines_is_refused() {
    let (_home, store) = enabled("records-damaged-seq");
    let records = small("one");
    part(&store, "c-1", "d", records.clone(), true).unwrap();
    let file = store
        .conversation_dir("c-1")
        .unwrap()
        .join("records/1.jsonl");
    let mut odd = records[0].clone();
    odd.seq = u64::MAX;
    fs::write(&file, super::super::drafts::line(&odd)).unwrap();
    let refused = store
        .read_records("c-1", &RecordRead::default(), &mut |_| Ok(()))
        .unwrap_err()
        .text();
    assert!(
        refused.contains("records line 1 holds record 18446744073709551615"),
        "{refused}"
    );
}
