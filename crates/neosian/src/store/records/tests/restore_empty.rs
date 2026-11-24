//! A redacted skeleton and a message with empty text have different record counts.
use super::restore_source::{ID, Sent, request, send, staged, stamp};
use super::{LIMITS, enabled, messages};
use crate::conversation::turn_records::{
    Header, ReadItem, RecordRead, Restore, Staged, records_of, restore_id,
};
use crate::conversation::{Error, message};
use crate::store::FileStore;
use serde_json::json;

fn empty_header(turn: u64, redacted: bool) -> Header {
    Header {
        turn,
        created_at: stamp("2026-11-24T16:00:00Z"),
        actor: Some("cli:archive".into()),
        redacted,
        records: 0,
        bytes: 0,
    }
}

fn part(items: &[Sent], commit: bool) -> Restore {
    Restore {
        commit,
        ..request(items)
    }
}

fn refuse(store: &FileStore, items: &[Sent], commit: bool, turn: u64) {
    let result = send(store, part(items, commit));
    assert!(
        matches!(result, Err(Error::Value(ref why)) if why.contains(&format!("turn {turn}"))),
        "{result:?}"
    );
}

#[test]
fn an_invalid_first_header_cannot_stage_or_publish() {
    for commit in [false, true] {
        let (_home, store) = enabled("restore-invalid-empty-turn");
        refuse(&store, &[Sent::Header(empty_header(1, false))], commit, 1);
        assert_eq!(send(&store, request(&[])).unwrap().state, Staged::None);
        assert!(!store.staging(ID, &restore_id(ID)).unwrap().dir.exists());
        assert!(store.conversations().unwrap().is_empty());
        assert_eq!(store.last_turn_number(ID).unwrap(), 0);
    }
}

#[test]
fn a_refused_part_leaves_the_previous_staging_intact() {
    let (home, store) = enabled("restore-empty-part");
    send(&store, request(&[Sent::Header(empty_header(1, true))])).unwrap();
    let state = store.staging(ID, &restore_id(ID)).unwrap().state;
    let original = std::fs::read(&state).unwrap();
    let files = staged(&store);
    let items = [
        Sent::Header(empty_header(2, true)),
        Sent::Header(empty_header(3, false)),
    ];
    for commit in [false, true] {
        refuse(&store, &items, commit, 3);
        assert_eq!(std::fs::read(&state).unwrap(), original);
        assert_eq!(staged(&store), files);
    }
    drop(store);
    let store = FileStore::open(home.path()).unwrap();
    let done = send(&store, part(&[], true)).unwrap();
    assert_eq!(done.state, Staged::Committed);
    assert_eq!(store.last_turn_number(ID).unwrap(), 1);
}

#[test]
fn a_skeleton_and_an_empty_message_survive_reopen_and_fold() {
    let messages = messages(&[json!({"role":"user","content":""})]);
    let records = records_of(&messages).unwrap();
    assert_eq!(records.len(), 1);
    let skeleton = empty_header(1, true);
    let present = Header::of(2, skeleton.created_at, skeleton.actor.clone(), &records);
    assert_eq!((present.records, present.bytes), (1, 0));
    let record = records.into_iter().next().unwrap();
    let items = [
        Sent::Header(skeleton.clone()),
        Sent::Header(present.clone()),
        Sent::Record(2, record.clone()),
    ];
    let expected = [
        ReadItem::Header(skeleton),
        ReadItem::Header(present),
        ReadItem::Record {
            turn: 2,
            record,
            tail: None,
            truncated: false,
        },
    ];
    for immediate in [false, true] {
        let (home, store) = enabled("restore-valid-empty-turns");
        let answer = send(&store, part(&items, immediate)).unwrap();
        if !immediate {
            assert_eq!(answer.state, Staged::Open);
            assert_eq!(store.last_turn_number(ID).unwrap(), 0);
        }
        drop(store);
        let store = FileStore::open(home.path()).unwrap();
        let done = send(&store, part(&[], true)).unwrap();
        assert_eq!(done.state, Staged::Committed);
        if immediate {
            assert_eq!(answer.state, Staged::Committed);
            assert_eq!(answer.digest, done.digest);
        }
        let check = |store: &FileStore| {
            let rows = store.read_turns(ID, 0, None).unwrap();
            assert_eq!(rows.len(), 2);
            assert!(rows[0].redacted && rows[0].messages.is_empty());
            assert!(!rows[1].redacted && rows[1].messages.len() == 1);
            assert_eq!(
                message::to_json(&rows[1].messages[0]),
                message::to_json(&messages[0])
            );
            let mut found = vec![];
            let next = store
                .read_records(ID, &RecordRead::default(), &mut |item| {
                    found.push(item);
                    Ok(())
                })
                .unwrap();
            assert!(next.is_none());
            assert_eq!(found, expected);
        };
        check(&store);
        let stats = store
            .fold_records(ID, &mut store.record_places().unwrap(), false, &LIMITS)
            .unwrap();
        assert_eq!((stats.folded, stats.skeletons), (1, 1));
        drop(store);
        check(&FileStore::open(home.path()).unwrap());
    }
}
