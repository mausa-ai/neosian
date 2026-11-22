//! Numbers on a FileStore (§40.54 items 9 and 10, the review of 2481787,
//! findings 2 and 3): a number the read refuses never reaches a seal, so a
//! turn a seal makes visible always rebuilds; a wrapping size never passes
//! the stated total or the store's bound; either request stores nothing.
//! A read's numbers, a continuation's included, are taken with checks.

use std::fs;

use serde_json::json;

use super::reading::{SESSION, mixed};
use super::{ACTOR, LIMITS, enabled, messages, part, turns};
use crate::conversation::message::Role;
use crate::conversation::turn_records::{Form, Kind, ReadPosition, RecordRead, TurnRecord, Write};
use crate::limits;

fn call(text: &str) -> TurnRecord {
    TurnRecord {
        seq: 0,
        message: 0,
        role: Role::Assistant,
        kind: Kind::ToolCall,
        content: Some(Form::None),
        block: None,
        call: Some(0),
        call_id: Some("c".into()),
        name: Some("n".into()),
        chunk: 0,
        size: text.len() as u64,
        text: text.into(),
    }
}

#[test]
fn a_number_the_read_refuses_never_seals() {
    let (_home, store) = enabled("records-rebuilds-number");
    store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "before"})]),
            None,
        )
        .unwrap();
    let refused = part(
        &store,
        "c-1",
        "d",
        vec![call(r#"{"a":1.7976931348623158e308}"#)],
        true,
    )
    .unwrap_err()
    .text();
    assert!(refused.contains("a number out of range"), "{refused}");
    assert_eq!(turns(&store, "c-1").len(), 1);
    let drafts = store.conversation_dir("c-1").unwrap().join(".drafts");
    assert!(!drafts.join("d.state").exists() && !drafts.join("d.jsonl").exists());
    let sealed = part(
        &store,
        "c-1",
        "d",
        vec![call(r#"{"a":1.7976931348623157e+308}"#)],
        true,
    )
    .unwrap();
    assert_eq!(sealed.turn.unwrap().turn, 2);
    let read = turns(&store, "c-1");
    assert_eq!(read[1].messages[0].tool_calls[0].arguments["a"], f64::MAX);
}

/// Run with overflow checks off as well: there the old sum wrapped to 0
/// and the draft took 3.9 MB under a stated 100 and a 65,536-byte bound.
#[test]
fn a_size_that_would_wrap_is_refused_and_stores_nothing() {
    let (_home, store) = enabled("records-rebuilds-wrap");
    let small = limits::Records {
        max_record_turn_bytes: 65_536,
        ..LIMITS
    };
    let mut records = vec![TurnRecord {
        seq: 0,
        message: 0,
        role: Role::User,
        kind: Kind::Prompt,
        content: Some(Form::Text),
        block: None,
        call: None,
        call_id: None,
        name: None,
        chunk: 0,
        size: 1,
        text: "x".into(),
    }];
    for chunk in 0..3_u64 {
        records.push(TurnRecord {
            seq: chunk + 1,
            message: 1,
            role: Role::Tool,
            kind: Kind::ToolResult,
            content: (chunk == 0).then_some(Form::Text),
            chunk,
            size: u64::MAX,
            text: "a".repeat(65_000),
            ..records[0].clone()
        });
    }
    let write = Write {
        draft: "w".into(),
        records,
        turn_bytes: Some(100),
        actor: Some(ACTOR.into()),
        ..Write::default()
    };
    let refused = store
        .write_records("c-1", write, &small)
        .unwrap_err()
        .text();
    assert!(refused.contains("overflows the draft's bytes"), "{refused}");
    let drafts = store.conversation_dir("c-1").unwrap().join(".drafts");
    assert!(fs::read_dir(&drafts).map_or(true, |mut entries| entries.next().is_none()));
}

/// A read's `after` at the top of its range, and continuations naming the
/// largest numbers at a line boundary: answered, or refused by name (a
/// cursor holds integers up to 2^63 - 1), never a panic or a wrapped number
/// (finding 3 in T1b1b's read).
#[test]
fn a_reads_numbers_at_their_limits_are_taken_with_checks() {
    let (_home, store) = mixed("records-numbers-read");
    let mut visit = |_| Ok(());
    let after = RecordRead {
        after: u64::MAX,
        ..RecordRead::default()
    };
    let refused = store
        .read_records(SESSION, &after, &mut visit)
        .unwrap_err()
        .text();
    assert!(refused.contains("after"), "{refused}");
    let records = store
        .conversation_dir(SESSION)
        .unwrap()
        .join("records/3.jsonl");
    let second = fs::read_to_string(&records).unwrap().find('\n').unwrap() as u64 + 1;
    let read = RecordRead::default();
    for position in [
        ReadPosition {
            turn: 3,
            seq: i64::MAX as u64,
            record_offset: second,
            ..ReadPosition::default()
        },
        ReadPosition {
            turn: 3,
            seq: u64::MAX,
            record_offset: second,
            ..ReadPosition::default()
        },
        ReadPosition {
            turn: u64::MAX,
            seq: u64::MAX,
            log_offset: u64::MAX,
            log_line: u64::MAX,
            record_offset: u64::MAX,
        },
    ] {
        let resumed = RecordRead {
            continuation: Some(position.encode(&read.fingerprint(SESSION))),
            ..read.clone()
        };
        let answer = store.read_records(SESSION, &resumed, &mut |_| Ok(()));
        if let Err(error) = answer {
            let text = error.text();
            assert!(
                text.contains("malformed records-v1/read cursor"),
                "{position:?}: {text}"
            );
        }
    }
}
