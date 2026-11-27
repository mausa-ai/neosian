//! A records store's files, after the review of 2481787: every new path is
//! held to the store's containment check (finding 7), temp files a killed
//! write left are swept and a missing records file is refused by name
//! (finding 8), a seal marker on a store without records is a named
//! refusal and never rolled forward (finding 12), and the mutations M3 and
//! M7 caught. Each holds for the reads built after 2481787 too: the record
//! read and history search.

use std::fs;
#[cfg(unix)]
use std::os::unix::fs::symlink;

use serde_json::json;

use super::super::seal::CRASH_AFTER;
use super::reading::{SESSION, mixed};
use super::writing::store_path;
use super::{enabled, example, messages, part, turns, written};
use crate::conversation::Turn;
use crate::conversation::message::text_of;
use crate::conversation::rank::{Order, Request};
use crate::conversation::turn_records::{RecordRead, records_of};
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;

/// Every read of conversation c-1 through the doors a records turn has:
/// the legacy read, the record read and history search.
fn reads(store: &FileStore) -> Vec<Result<(), String>> {
    let request = Request {
        terms: vec!["tests".into()],
        ids: None,
        limit: 10,
        order: Order::Newest,
        since: None,
        before: None,
    };
    let third = RecordRead {
        turn: Some(3),
        ..RecordRead::default()
    };
    vec![
        store
            .read_turns(SESSION, 0, None)
            .map(drop)
            .map_err(|e| e.text()),
        store
            .read_records(SESSION, &third, &mut |_| Ok(()))
            .map(drop)
            .map_err(|e| e.text()),
        store.rank_turns(&request).map(drop).map_err(|e| e.text()),
    ]
}

#[cfg(unix)]
#[test]
fn every_new_path_stays_inside_the_store() {
    let (_home, store) = enabled("records-files-drafts");
    let outside = Scratch::new("records-files-outside");
    store
        .append_turn(
            SESSION,
            messages(&[json!({"role": "user", "content": "one"})]),
            None,
        )
        .unwrap();
    symlink(
        outside.path(),
        store.conversation_dir(SESSION).unwrap().join(".drafts"),
    )
    .unwrap();
    let records = records_of(&messages(&[json!({"role": "user", "content": "secret"})])).unwrap();
    let refused = part(&store, SESSION, "d", records, true)
        .unwrap_err()
        .text();
    assert!(refused.contains("escapes the store root"), "{refused}");
    assert_eq!(fs::read_dir(outside.path()).unwrap().count(), 0);

    let (_home, store) = mixed("records-files-records");
    let directory = store.conversation_dir(SESSION).unwrap();
    let elsewhere = Scratch::new("records-files-elsewhere");
    fs::rename(directory.join("records"), elsewhere.path().join("records")).unwrap();
    symlink(elsewhere.path().join("records"), directory.join("records")).unwrap();
    for read in reads(&store) {
        let refused = read.unwrap_err();
        assert!(refused.contains("escapes the store root"), "{refused}");
    }

    let home = Scratch::new("records-files-marker");
    let store = FileStore::open(home.path()).unwrap();
    let foreign = Scratch::new("records-files-foreign");
    foreign.plant("format", "{\"neosian_records\":1}\n");
    symlink(foreign.path(), home.path().join(".records")).unwrap();
    for refused in [
        store.records_enabled().map(drop).unwrap_err().text(),
        store.enable_records().map(drop).unwrap_err().text(),
    ] {
        assert!(refused.contains("escapes the store root"), "{refused}");
    }
}

#[test]
fn temp_files_are_swept_and_a_missing_records_file_is_named() {
    let (home, store) = mixed("records-files-temps");
    let temp = ".neosian-tmp-1-0000abcd";
    home.plant(&format!("conversations/{SESSION}/.drafts/{temp}"), "half");
    home.plant(&format!(".records/{temp}"), "half");
    let records = records_of(&messages(&[json!({"role": "user", "content": "x"})])).unwrap();
    part(&store, SESSION, "e", records, false).unwrap();
    assert!(!store_path(&store, SESSION, temp).exists());
    assert!(!store.enable_records().unwrap());
    assert!(!home.path().join(".records").join(temp).exists());

    fs::remove_file(
        store
            .conversation_dir(SESSION)
            .unwrap()
            .join("records/3.jsonl"),
    )
    .unwrap();
    for read in reads(&store) {
        let refused = read.unwrap_err();
        assert!(
            refused.contains("agent_conversation_format_unsupported")
                && refused.contains("turn 3: its records file is missing"),
            "{refused}"
        );
    }
}

/// Finding 12: a seal marker on a store without the records marker (the
/// marker removed, or planted) is refused by name, and nothing is rolled
/// forward: no format-2 header the store would then refuse to read.
#[test]
fn a_seal_marker_without_records_is_refused_by_name() {
    let home = Scratch::new("records-files-no-records");
    let store = FileStore::open(home.path()).unwrap();
    store
        .append_turn(
            SESSION,
            messages(&[json!({"role": "user", "content": "one"})]),
            None,
        )
        .unwrap();
    let log = store.conversation_dir(SESSION).unwrap().join("turns.jsonl");
    let before = fs::read_to_string(&log).unwrap();
    let header = r#"{"neosian_format":2,"turn":2,"created_at":"2026-11-21T14:03:07.512000Z","actor":null,"records":1,"bytes":1,"draft":"d"}"#;
    home.plant(
        &format!("conversations/{SESSION}/.drafts/sealing"),
        &format!("{header}\n"),
    );
    let refusals = [
        store
            .append_turn(
                SESSION,
                messages(&[json!({"role": "user", "content": "two"})]),
                None,
            )
            .map(drop),
        store.read_turns(SESSION, 0, None).map(drop),
        store.last_turn_number(SESSION).map(drop),
        store.first_turn_stamp(SESSION).map(drop),
        store.restore_conversation(SESSION, &[], &[], &[]),
    ];
    for refused in refusals {
        let refused = refused.unwrap_err().text();
        assert!(
            refused.contains("a seal marker is left on a store without records")
                && refused.contains("neosian records enable")
                && refused.contains(".drafts"),
            "{refused}"
        );
    }
    assert_eq!(fs::read_to_string(&log).unwrap(), before);
}

/// Mutation M3: restoring a conversation rolls a marked seal forward first,
/// so the sealed turn occupies it and the restore is refused.
#[test]
fn a_restore_rolls_a_marked_seal_forward_first() {
    let (_home, store) = enabled("records-files-restore");
    let records = records_of(&messages(&[json!({"role": "user", "content": "sealed"})])).unwrap();
    CRASH_AFTER.with(|crash| crash.set(2));
    assert!(part(&store, SESSION, "d", records, true).is_err());
    CRASH_AFTER.with(|crash| crash.set(0));
    let restored = Turn {
        conversation_id: SESSION.into(),
        turn: 1,
        messages: messages(&[json!({"role": "user", "content": "restored"})]),
        created_at: Stamp::now(),
        actor: None,
        redacted: false,
    };
    let refused = store
        .restore_conversation(SESSION, &[restored], &[], &[])
        .unwrap_err()
        .text();
    assert!(refused.contains("target_occupied"), "{refused}");
    let read = turns(&store, SESSION);
    assert_eq!(read.len(), 1);
    assert_eq!(text_of(&read[0].messages[0]), "sealed");
}

/// Mutation M7: a header whose bytes differ from its records' is refused,
/// though their count agrees.
#[test]
fn a_header_whose_bytes_differ_from_its_records_is_refused() {
    let (_home, store) = enabled("records-files-bytes");
    written(&store, SESSION, "d", &example());
    let log = store.conversation_dir(SESSION).unwrap().join("turns.jsonl");
    let text = fs::read_to_string(&log).unwrap();
    fs::write(&log, text.replace("\"bytes\":812400", "\"bytes\":812399")).unwrap();
    let refused = store.read_turns(SESSION, 0, None).unwrap_err().text();
    assert!(
        refused.contains("turn 1: its records do not match its header"),
        "{refused}"
    );
}
