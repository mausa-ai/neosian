//! Empty and sparse transfers, independent receipts, and metadata insertion order.
use super::*;
use crate::conversation::turn_records::{Position, Write, records_of};
use crate::conversation::{Projection, Redaction};

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_zero_item_unit_creates_the_parent_once_and_its_receipt_is_scoped_by_name_and_conversation() {
    let db = Database::new();
    let empty = commit(request(NAME));
    let done = send(&db.store, empty.clone()).unwrap();
    assert_eq!(
        done.to_json(),
        json!({"restore":NAME, "state":"committed",
        "position":{"turn":0,"seq":0,"complete":true,"projections":0,"redactions":0},
        "stored_bytes":1637,"freed_bytes":1071,
        "digest":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"})
    );
    assert_eq!(rows(&db, "conversations").len(), 1);
    assert!(db.store.conversations().unwrap().is_empty());
    assert!(db.store.read_turns(ID, 0, None).unwrap().is_empty());
    assert!(rows(&db, "record_turns").is_empty());
    let reopened = db.another_store();
    assert_replay(send(&reopened, empty.clone()).unwrap(), &done);
    // A committed marker alone leaves this target eligible for another restore.
    let another = send(&reopened, commit(request("next"))).unwrap();
    assert_eq!(another.position, Some(Position::default()));
    assert_eq!((another.stored_bytes, another.freed_bytes), (1316, 1042));
    let (first, _) = example();
    let elsewhere = reopened
        .restore_records("other", commit(first), &LIMITS)
        .unwrap();
    assert_ne!(elsewhere.digest, done.digest);
    assert_eq!(reopened.conversations().unwrap(), ["other"]);
    assert_replay(send(&reopened, request(NAME)).unwrap(), &done);
    assert_replay(
        reopened.restore_records("other", empty, &LIMITS).unwrap(),
        &elsewhere,
    );
    assert_replay(send(&reopened, request("next")).unwrap(), &another);
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn only_redacted_sparse_turns_preserve_source_order_stamps_and_nullable_actors() {
    let db = Database::new();
    let home = Scratch::new("pg-restore-sparse-skeletons");
    let file = FileStore::open(home.path()).unwrap();
    file.enable_records().unwrap();
    let mut early = header(17, "2026-04-29T12:13:14.123456Z", 0, 0, true);
    early.actor = None;
    let later = header(301, "2020-01-01T00:00:00Z", 0, 0, true);
    let mut part = request(NAME);
    part.turns = vec![early, later];
    part.projections_from = Some(0);
    part.projections = ["third", "first", "second"]
        .map(|s| Projection {
            turn: 301,
            span: 1,
            kind: "log".into(),
            text: s.into(),
        })
        .to_vec();
    part.redactions_from = Some(0);
    part.redactions = [Some("a"), None, Some("b")]
        .map(|actor| Redaction {
            conversation_id: ID.into(),
            turns: vec![301, 17],
            actor: actor.map(str::to_string),
            created_at: stamp("2026-06-01T04:03:02.123456Z"),
        })
        .to_vec();
    let mut opening = part.clone();
    opening.redactions.pop();
    assert_eq!(
        send(&db.store, opening.clone()).unwrap(),
        file.restore_records(ID, opening, &LIMITS).unwrap()
    );
    // The final request repeats earlier metadata at nonzero offsets and adds the last act.
    let final_part = commit(Restore {
        projections_from: Some(1),
        projections: part.projections[1..].to_vec(),
        redactions_from: Some(1),
        redactions: part.redactions[1..].to_vec(),
        ..request(NAME)
    });
    let done = send(&db.store, final_part.clone()).unwrap();
    assert_eq!(
        file.restore_records(ID, final_part.clone(), &LIMITS)
            .unwrap(),
        done
    );
    assert_replay(
        send(&db.another_store(), final_part.clone()).unwrap(),
        &done,
    );
    let mut changed = final_part;
    changed.redactions_from = Some(0);
    assert_conflict(
        send(&db.store, changed),
        "restore_committed",
        json!({"position":done.position.unwrap().to_json()}),
    );
    let got = db.store.read_turns(ID, 0, None).unwrap();
    assert_eq!(got.len(), 2);
    for (t, h) in got.iter().zip(&part.turns) {
        assert!(t.redacted && t.messages.is_empty());
        assert_eq!(
            (t.turn, t.created_at, &t.actor),
            (h.turn, h.created_at, &h.actor)
        );
    }
    assert_eq!(db.store.last_turn_number(ID).unwrap(), 301);
    assert_eq!(
        db.store.read_projections(ID, 0, None).unwrap(),
        part.projections
    );
    let reverse = part.redactions.into_iter().rev().collect::<Vec<_>>();
    assert_eq!(db.store.turn_redactions(None, None, 10).unwrap(), reverse);
    assert_eq!(file.turn_redactions(None, None, 10).unwrap(), reverse);
    assert!(rows(&db, "turn_records").is_empty());
    assert!(rows(&db, "record_turns").is_empty());
    assert!(
        rows(&db, "turns")
            .iter()
            .all(|r| r["neosian_format"] == 1 && r["search_text"] == "")
    );
    assert!(
        db.query(
            "SELECT created_at='2026-04-29T12:13:14.123456Z'::timestamptz
             FROM {{schema}}.conversations"
        )[0]
        .get::<_, bool>(0)
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_restore_commit_preserves_a_same_named_open_write_and_another_conversations_restore() {
    let db = Database::new();
    let (mut first, _) = example();
    first.turns[0].turn = 19;
    for (turn, _) in &mut first.records {
        *turn = 19;
    }
    let write = Write {
        draft: NAME.into(),
        records: records_of(&[message("user", "private")]).unwrap(),
        ..Write::default()
    };
    db.store.write_records(ID, write, &LIMITS).unwrap();
    db.store
        .restore_records("other", first.clone(), &LIMITS)
        .unwrap();
    let other_rows = snapshot(&db);
    send(&db.store, commit(first)).unwrap();
    let now = snapshot(&db);
    for (before, after) in other_rows.iter().zip(now) {
        for old in before {
            assert!(after.contains(old));
        }
    }
    assert_eq!(db.store.conversations().unwrap(), [ID]);
    let sealed = db
        .store
        .write_records(
            ID,
            Write {
                draft: NAME.into(),
                seal: true,
                ..Write::default()
            },
            &LIMITS,
        )
        .unwrap();
    assert_eq!(sealed.turn.unwrap().turn, 20);
    let other = db
        .store
        .restore_records("other", commit(request(NAME)), &LIMITS)
        .unwrap();
    assert_eq!(other.state, Staged::Committed);
    assert_eq!(db.store.read_turns("other", 0, None).unwrap()[0].turn, 19);
    let mapped = db.query(
        "SELECT turn,part,draft FROM {{schema}}.record_turns
                           WHERE conversation_id='other'",
    );
    assert_eq!(mapped.len(), 1);
    assert_eq!(
        (
            mapped[0].get::<_, i32>(0),
            mapped[0].get::<_, i32>(1),
            mapped[0].get::<_, &str>(2)
        ),
        (19, 19, NAME)
    );
    assert_eq!(db.store.conversations().unwrap(), [ID, "other"]);
}
