//! Publication and durable replay across PostgreSQL sessions (§40.54 item 12).
mod edge_cases;
mod refusals;

use super::Database;
use super::restore_stage::{
    ID, LIMITS, NAME, assert_conflict, assert_invisible, example, header, message, request, rows,
    send, snapshot, stamp,
};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    ReadItem, RecordRead, RecordSearch, Restore, Restored, Staged,
};
use crate::postgres::PostgresStore;
use crate::store::FileStore;
use crate::testing::Scratch;
use crate::wire::encode;
use serde_json::json;

const DIGEST: &str = "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32";

fn commit(part: Restore) -> Restore {
    Restore {
        commit: true,
        ..part
    }
}

fn assert_replay(reply: Restored, original: &Restored) {
    assert_eq!(reply.state, Staged::Committed);
    assert_eq!(reply.restore, original.restore);
    assert_eq!(reply.position, original.position);
    assert_eq!(reply.digest, original.digest);
    assert_eq!((reply.stored_bytes, reply.freed_bytes), (0, 0));
}

fn verify_visible(pg: &PostgresStore, file: &FileStore, first: &Restore, last: &Restore) {
    assert_eq!(pg.conversations().unwrap(), [ID]);
    assert_eq!(pg.last_turn_number(ID).unwrap(), 3);
    let turns = pg.read_turns(ID, 0, None).unwrap();
    let file_turns = file.read_turns(ID, 0, None).unwrap();
    assert_eq!(
        turns.iter().map(encode::turn).collect::<Vec<_>>(),
        file_turns.iter().map(encode::turn).collect::<Vec<_>>()
    );
    assert_eq!(turns.len(), 3);
    let mut expected = Vec::new();
    for (turn, h) in turns.iter().zip(first.turns.iter().chain(&last.turns)) {
        assert_eq!(turn.conversation_id, ID);
        assert_eq!(
            (turn.turn, turn.created_at, &turn.actor, turn.redacted),
            (h.turn, h.created_at, &h.actor, h.redacted)
        );
        expected.push(ReadItem::Header(h.clone()));
        for (number, record) in first
            .records
            .iter()
            .chain(&last.records)
            .filter(|(t, _)| *t == h.turn)
        {
            expected.push(ReadItem::Record {
                turn: *number,
                record: record.clone(),
                tail: None,
                truncated: false,
            });
        }
    }
    assert!(turns[1].messages.is_empty());
    let mut found = Vec::new();
    let next = pg
        .read_records(ID, &RecordRead::default(), &mut |item| {
            found.push(item);
            Ok(())
        })
        .unwrap();
    assert!(next.is_none());
    assert_eq!(found, expected);
    assert_eq!(pg.read_projections(ID, 0, None).unwrap(), last.projections);
    assert_eq!(pg.turn_redactions(None, None, 10).unwrap(), last.redactions);
    assert_eq!(
        pg.search_turns(&["www".into()], None, 10).unwrap()[0].turn,
        3
    );
    let search = RecordSearch::new("www", None, Order::Newest, None, None, 10).unwrap();
    let hits = pg.search_records(&search).unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(
        (hits[0].conversation_id.as_str(), hits[0].turn, hits[0].seq),
        (ID, 3, 0)
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn item_12_restores_the_complete_unit_and_a_reopen_recognizes_the_final_part() {
    let db = Database::new();
    let home = Scratch::new("pg-restore-published-example");
    let file = FileStore::open(home.path()).unwrap();
    assert!(file.enable_records().unwrap());
    let (first, last) = example();
    let opening = send(&db.store, first.clone()).unwrap();
    assert_eq!(opening.stored_bytes, 4002);
    assert_eq!(
        file.restore_records(ID, first.clone(), &LIMITS).unwrap(),
        opening
    );
    assert_invisible(&db);
    let finished = commit(last);
    let done = send(&db.store, finished.clone()).unwrap();
    assert_eq!(
        done.to_json().to_string(),
        r#"{"restore":"iecf9f710d465bc0ea0fe2fbba8915596","state":"committed","position":{"turn":3,"seq":1,"complete":true,"projections":1,"redactions":1},"stored_bytes":2077,"freed_bytes":1071,"digest":"6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32"}"#
    );
    assert_eq!(
        file.restore_records(ID, finished.clone(), &LIMITS).unwrap(),
        done
    );
    let reopened = db.another_store();
    verify_visible(&reopened, &file, &first, &finished);
    let before = snapshot(&db);
    assert_eq!(before[0].len(), 1);
    assert_eq!(before[0][0]["state"], "committed");
    assert_eq!(before[0][0]["bytes"], 303);
    assert!(before[1].is_empty());
    assert_eq!(before[2].len(), 4);
    let later = reopened
        .append_turn(ID, vec![message("user", "later")], None)
        .unwrap();
    assert_eq!(later.turn, 4);
    let before_visible = [
        "turns",
        "record_turns",
        "projections",
        "turn_redactions",
        "conversations",
    ]
    .map(|table| rows(&db, table));
    for retry in [finished.clone(), request(NAME), commit(request(NAME))] {
        assert_replay(send(&reopened, retry).unwrap(), &done);
    }
    let mut wrong_projection = finished.clone();
    wrong_projection.projections_from = Some(1);
    let mut wrong_act = finished.clone();
    wrong_act.redactions_from = Some(1);
    let mut changed_record = finished.clone();
    changed_record.records[0].1.text = "q".repeat(44);
    let mut after_commit = finished;
    after_commit.commit = false;
    for refused in [
        wrong_projection,
        wrong_act,
        changed_record,
        commit(first),
        after_commit,
        Restore {
            discard: true,
            ..request(NAME)
        },
    ] {
        assert_conflict(
            send(&reopened, refused),
            "restore_committed",
            json!({"position": done.position.unwrap().to_json()}),
        );
    }
    assert_eq!(snapshot(&db), before);
    assert_eq!(
        [
            "turns",
            "record_turns",
            "projections",
            "turn_redactions",
            "conversations"
        ]
        .map(|table| rows(&db, table)),
        before_visible
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn an_empty_final_request_sets_its_own_replay_boundary_and_preserves_an_existing_parent() {
    let db = Database::new();
    db.execute("INSERT INTO {{schema}}.conversations VALUES ('cc-7f3a','2005-01-02T00:00:00Z')");
    let (first, last) = example();
    assert_eq!(send(&db.store, first).unwrap().stored_bytes, 4002);
    assert_eq!(send(&db.store, last.clone()).unwrap().stored_bytes, 1511);
    let done = send(&db.store, commit(request(NAME))).unwrap();
    assert_eq!(
        (done.state, done.stored_bytes, done.freed_bytes),
        (Staged::Committed, 303, 1071)
    );
    assert_eq!(done.digest.as_deref(), Some(DIGEST));
    let unchanged: bool = db.query(
        "SELECT created_at='2005-01-02T00:00:00Z'::timestamptz FROM {{schema}}.conversations",
    )[0]
    .get(0);
    assert!(unchanged);
    let reopened = db.another_store();
    assert_replay(send(&reopened, commit(request(NAME))).unwrap(), &done);
    assert_conflict(
        send(&reopened, commit(last)),
        "restore_committed",
        json!({"position":done.position.unwrap().to_json()}),
    );
}
