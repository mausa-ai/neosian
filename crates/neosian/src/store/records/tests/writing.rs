//! Writing in parts on a FileStore: the round trip through `read_turns`,
//! one number sequence with whole turns, drafts nobody sees, every conflict
//! and refusal by name, and the size rule's bytes in each answer.

use std::fs;

use serde_json::{Value, json};

use super::{ACTOR, LIMITS, enabled, example, messages, part, spelled, turns, written};
use crate::conversation::rows_header::Row;
use crate::conversation::turn_records::{Sizes, State, Write, records_of};
use crate::store::FileStore;

#[test]
fn a_turn_written_in_parts_reads_back_as_the_same_messages_appended_whole() {
    let (_home, store) = enabled("records-round-trip");
    let first = messages(&[json!({"role": "user", "content": "before"})]);
    let whole = store
        .append_turn("c-1", first, Some("cli:t".into()))
        .unwrap();
    let sealed = written(&store, "c-1", "d7c1e0b2a9f44e57", &example());
    let header = sealed.turn.clone().unwrap();
    assert_eq!(
        (
            sealed.state,
            sealed.next_seq,
            header.turn,
            header.records,
            header.bytes
        ),
        (State::Sealed, 16, 2, 16, 812_400)
    );
    assert_eq!(header.actor.as_deref(), Some(ACTOR));
    let after = store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "after"})]),
            None,
        )
        .unwrap();
    assert_eq!((whole.turn, after.turn), (1, 3), "one gapless sequence");
    let read = turns(&store, "c-1");
    assert_eq!(read.iter().map(|t| t.turn).collect::<Vec<_>>(), [1, 2, 3]);
    assert_eq!(spelled(&read[1].messages), spelled(&example()));
    assert_eq!(
        (
            read[1].created_at,
            read[1].actor.as_deref(),
            read[1].redacted
        ),
        (header.created_at, Some(ACTOR), false)
    );
    let appended = store
        .append_turn("c-2", example(), Some(ACTOR.into()))
        .unwrap();
    let mut both = [
        crate::wire::encode::turn(&read[1]),
        crate::wire::encode::turn(&turns(&store, "c-2")[0]),
    ];
    for turn in &mut both {
        turn["created_at"] = Value::Null;
        turn["conversation_id"] = Value::Null;
        turn["turn"] = Value::Null;
    }
    assert_eq!(
        crate::pyfmt::dumps(&both[0], true),
        crate::pyfmt::dumps(&both[1], true),
        "byte for byte as appended whole"
    );
    assert_eq!(appended.turn, 1);
    assert_eq!(store.last_turn_number("c-1").unwrap(), 3);
    let page = store.read_turns_page("c-1", 1, Some(1), None, &mut |turn| {
        assert_eq!(spelled(&turn.messages), spelled(&example()));
        Ok(true)
    });
    assert!(page.is_ok());
}

#[test]
fn no_read_numbering_or_listing_sees_an_open_draft() {
    let (_home, store) = enabled("records-invisible");
    store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "seen"})]),
            None,
        )
        .unwrap();
    let records = records_of(&messages(&[
        json!({"role": "user", "content": "unseen words"}),
    ]))
    .unwrap();
    part(&store, "c-1", "open", records.clone(), false).unwrap();
    part(&store, "c-only-draft", "open", records, false).unwrap();
    assert_eq!(turns(&store, "c-1").len(), 1);
    assert_eq!(store.last_turn_number("c-1").unwrap(), 1);
    assert_eq!(store.last_turn_number("c-only-draft").unwrap(), 0);
    assert!(turns(&store, "c-only-draft").is_empty());
    assert_eq!(store.first_turn_stamp("c-only-draft").unwrap(), None);
    assert_eq!(store.conversations().unwrap(), ["c-1"]);
    assert!(
        store
            .search_turns(&["unseen".into()], None, 10)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn probes_repeats_discards_and_stale_drafts_answer_as_item_9_says() {
    let (_home, store) = enabled("records-probes");
    let records = records_of(&example()).unwrap();
    let sizes = Sizes::new("c-1", "d1");
    let probe = |store: &FileStore| part(store, "c-1", "d1", Vec::new(), false).unwrap();
    assert_eq!(
        (probe(&store).state, probe(&store).next_seq),
        (State::None, 0)
    );
    let first = part(&store, "c-1", "d1", records[..9].to_vec(), false).unwrap();
    let expected: u64 =
        sizes.bookkeeping() + records[..9].iter().map(|r| sizes.record(r)).sum::<u64>();
    assert_eq!(
        (first.state, first.next_seq, first.stored_bytes),
        (State::Open, 9, expected)
    );
    let again = part(&store, "c-1", "d1", records[5..12].to_vec(), false).unwrap();
    let added: u64 = records[9..12].iter().map(|r| sizes.record(r)).sum();
    assert_eq!(
        (again.next_seq, again.stored_bytes),
        (12, added),
        "repeats store nothing"
    );
    assert_eq!(
        (probe(&store).state, probe(&store).next_seq),
        (State::Open, 12)
    );
    let gone = store
        .write_records(
            "c-1",
            Write {
                draft: "d1".into(),
                discard: true,
                ..Write::default()
            },
            &LIMITS,
        )
        .unwrap();
    assert_eq!(
        (gone.state, gone.freed_bytes),
        (State::Discarded, expected + added)
    );
    assert_eq!(probe(&store).state, State::None);
    part(&store, "c-1", "old", records[..1].to_vec(), false).unwrap();
    let state = store_path(&store, "c-1", "old.state");
    let text = fs::read_to_string(&state).unwrap();
    let old: Value = serde_json::from_str(&text).unwrap();
    let aged = text.replace(old["touched_at"].as_str().unwrap(), "2026-01-01T00:00:00Z");
    fs::write(&state, aged).unwrap();
    let opened = part(&store, "c-1", "new", records[..1].to_vec(), false).unwrap();
    assert_eq!(
        opened.freed_bytes,
        old["bytes"].as_u64().unwrap(),
        "a stale draft is discarded at the next opening"
    );
    assert!(!state.exists());
}

pub(super) fn store_path(store: &FileStore, conversation: &str, name: &str) -> std::path::PathBuf {
    store
        .conversation_dir(conversation)
        .unwrap()
        .join(".drafts")
        .join(name)
}

#[test]
fn an_answer_writes_its_keys_in_the_order_item_9_lists() {
    let (_home, store) = enabled("records-answer");
    let sealed = written(
        &store,
        "c-1",
        "d",
        &messages(&[json!({"role": "user", "content": "q"})]),
    );
    let answer = sealed.to_json();
    let keys: Vec<&String> = answer.as_object().unwrap().keys().collect();
    assert_eq!(
        keys,
        [
            "draft",
            "state",
            "next_seq",
            "turn",
            "stored_bytes",
            "freed_bytes"
        ]
    );
    let turn: Vec<&String> = answer["turn"].as_object().unwrap().keys().collect();
    assert_eq!(
        turn,
        [
            "turn",
            "created_at",
            "actor",
            "redacted",
            "records",
            "bytes"
        ]
    );
    assert_eq!(answer["state"], "sealed");
    let sizes = Sizes::new("c-1", "d");
    let record = sizes
        .record(&records_of(&messages(&[json!({"role": "user", "content": "q"})])).unwrap()[0]);
    let stored =
        sizes.bookkeeping() + record + sizes.header(Some(ACTOR)) + sizes.conversation_row();
    assert_eq!(
        (sealed.stored_bytes, sealed.freed_bytes),
        (stored, sizes.bookkeeping()),
        "a seal creating the conversation"
    );
}

#[test]
fn a_legacy_read_past_the_turn_bound_is_refused_by_name_and_a_changed_record_by_its_digest() {
    let (_home, store) = enabled("records-legacy");
    written(&store, "c-1", "d", &example());
    let log = store.conversation_dir("c-1").unwrap().join("turns.jsonl");
    let row = |store: &FileStore| store.last_row(&log, "c-1").unwrap().unwrap();
    let refused = store.whole("c-1", row(&store), 65_536).unwrap_err().text();
    assert_eq!(
        refused,
        "resource limit NEOSIAN_MAX_TURN_BYTES exceeded: turn 1 of c-1 holds 812400 > 65536 bytes; a newer neosian reads this turn in parts"
    );
    assert!(store.whole("c-1", row(&store), 812_400).is_ok());
    let file = store
        .conversation_dir("c-1")
        .unwrap()
        .join("records/1.jsonl");
    let text = fs::read_to_string(&file).unwrap();
    fs::write(&file, text.replacen("run the tests", "run the tasks", 1)).unwrap();
    assert!(
        store
            .read_turns("c-1", 0, None)
            .unwrap_err()
            .text()
            .contains("records line 1 is unreadable")
    );
    assert!(matches!(row(&store), Row::Records(_)));
}
