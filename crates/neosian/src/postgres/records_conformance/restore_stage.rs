//! The §40.54 item 12 restore example before publication, across real SQL sessions.
mod ownership;
mod refusals;

use super::Database;
use crate::conversation::message::{self, Message};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    Header, Position, RecordRead, RecordSearch, Restore, RestoreStream, Restored, Staged,
    TurnRecord, Write, records_of,
};
use crate::conversation::{Error, Projection, Redaction};
use crate::limits::Records;
use crate::postgres::PostgresStore;
use crate::store::time::Stamp;
use serde_json::{Value, json};

const ID: &str = "cc-7f3a";
const NAME: &str = "iecf9f710d465bc0ea0fe2fbba8915596";
const LIMITS: Records = Records {
    draft_seconds: 60,
    max_turn_bytes: 64 << 20,
    max_record_turn_bytes: 1 << 30,
};

fn request(name: &str) -> Restore {
    Restore {
        restore: name.into(),
        ..Restore::default()
    }
}

fn message(role: &str, text: &str) -> Message {
    message::from_json(&json!({"role":role,"content":text})).unwrap()
}

fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

fn header(turn: u64, at: &str, records: u64, bytes: u64, redacted: bool) -> Header {
    Header {
        turn,
        created_at: stamp(at),
        actor: Some("claude-code:7f3a".into()),
        redacted,
        records,
        bytes,
    }
}

fn example() -> (Restore, Restore) {
    let first = records_of(&[
        message("system", &"x".repeat(32)),
        message("user", &"y".repeat(500)),
        message("assistant", &"z".repeat(1_000)),
    ])
    .unwrap();
    let first = Restore {
        turns: vec![header(1, "2026-10-02T09:15:00.000001Z", 3, 1532, false)],
        records: first.into_iter().map(|r| (1, r)).collect(),
        ..request(NAME)
    };
    let last = records_of(&[message("user", &"w".repeat(44))]).unwrap();
    let second = Restore {
        turns: vec![
            header(2, "2026-10-02T09:20:11Z", 0, 0, true),
            header(3, "2026-10-02T09:31:40Z", 1, 44, false),
        ],
        records: last.into_iter().map(|r| (3, r)).collect(),
        projections_from: Some(0),
        projections: vec![Projection {
            turn: 3,
            kind: "epoch".into(),
            text: "set up the cache".into(),
            span: 3,
        }],
        redactions_from: Some(0),
        redactions: vec![Redaction {
            conversation_id: ID.into(),
            turns: vec![2],
            actor: Some("client:default/ops".into()),
            created_at: stamp("2026-10-05T08:00:00Z"),
        }],
        ..request(NAME)
    };
    (first, second)
}

fn send(store: &PostgresStore, part: Restore) -> Result<Restored, Error> {
    store.restore_records(ID, part, &LIMITS)
}

fn rows(db: &Database, table: &str) -> Vec<Value> {
    let sql = format!(
        "SELECT to_jsonb(r) || jsonb_build_object('xmin',xmin::text,'ctid',ctid::text) AS data
         FROM {{{{schema}}}}.{table} r ORDER BY data"
    );
    db.query(&sql).into_iter().map(|r| r.get(0)).collect()
}

fn snapshot(db: &Database) -> [Vec<Value>; 3] {
    ["record_drafts", "record_staging", "turn_records"].map(|table| rows(db, table))
}

fn assert_conflict(result: Result<Restored, Error>, reason: &str, details: Value) {
    let Err(Error::Conversation(refused)) = result else {
        panic!("expected {reason}, got {result:?}");
    };
    assert_eq!(refused.code, "agent_conversation_conflict");
    assert_eq!(refused.details["reason"], reason);
    assert_eq!(refused.details["conversation_id"], ID);
    for (key, value) in details.as_object().unwrap() {
        assert_eq!(&refused.details[key], value);
    }
}

fn assert_invisible(db: &Database) {
    for table in [
        "conversations",
        "turns",
        "projections",
        "turn_redactions",
        "record_turns",
    ] {
        assert!(rows(db, table).is_empty(), "{table} contains staged data");
    }
    let store = &db.store;
    assert_eq!(store.last_turn_number(ID).unwrap(), 0);
    assert!(store.conversations().unwrap().is_empty());
    assert!(store.read_turns(ID, 0, None).unwrap().is_empty());
    assert!(store.read_projections(ID, 0, None).unwrap().is_empty());
    assert!(store.turn_redactions(None, None, 10).unwrap().is_empty());
    assert!(
        store
            .read_records(ID, &RecordRead::default(), &mut |_| {
                panic!("uncommitted read record")
            })
            .unwrap()
            .is_none()
    );
    assert!(
        store
            .search_turns(&["xxx".into()], None, 10)
            .unwrap()
            .is_empty()
    );
    for order in [Order::Newest, Order::Relevance] {
        let search = RecordSearch::new("xxx", None, order, None, None, 10).unwrap();
        assert!(store.search_records(&search).unwrap().is_empty());
    }
}

fn assert_metadata(db: &Database, owner: i64, first: &Restore, second: &Restore) {
    use crate::wire::encode;
    use sha2::{Digest, Sha256};

    let actual = db.query(
        "SELECT item,body,digest,owner FROM {{schema}}.record_staging
        ORDER BY CASE item WHEN 'turn' THEN 0 WHEN 'projection' THEN 1 ELSE 2 END, ord",
    );
    let mut expected = Vec::new();
    for h in first.turns.iter().chain(&second.turns) {
        expected.push(("turn", h.to_json(), Some((h.records, h.bytes))));
    }
    expected.push((
        "projection",
        encode::projection(&second.projections[0]),
        None,
    ));
    expected.push((
        "redaction",
        encode::turn_redaction(&second.redactions[0]),
        None,
    ));
    assert_eq!(actual.len(), expected.len());
    for (row, (kind, mut body, counters)) in actual.iter().zip(expected) {
        let digest = Sha256::digest(crate::pyfmt::dumps(&body, false).as_bytes());
        if let Some((count, bytes)) = counters {
            body["received_records"] = json!(count);
            body["received_bytes"] = json!(bytes);
        }
        assert_eq!(row.get::<_, &str>("item"), kind);
        assert_eq!(row.get::<_, i64>("owner"), owner);
        assert_eq!(row.get::<_, Value>("body"), body);
        assert_eq!(row.get::<_, &[u8]>("digest"), &digest[..]);
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn published_size_and_digest_survive_reopen_replay_and_discard() {
    let db = Database::new();
    let (first, second) = example();
    let store = &db.store;
    assert_eq!(send(store, request(NAME)).unwrap().state, Staged::None);
    assert!(snapshot(&db).iter().all(Vec::is_empty));
    assert_eq!(
        send(store, first.clone()).unwrap().to_json().to_string(),
        r#"{"restore":"iecf9f710d465bc0ea0fe2fbba8915596","state":"open","position":{"turn":1,"seq":3,"complete":true,"projections":0,"redactions":0},"stored_bytes":4002,"freed_bytes":0,"digest":null}"#
    );
    let owner: i64 = db.query("SELECT owner FROM {{schema}}.record_drafts")[0].get(0);
    let first_rows = rows(&db, "turn_records");
    let reopened = db.another_store();
    let mut overlap = second.clone();
    overlap.records.insert(0, first.records[2].clone());
    assert_eq!(
        send(&reopened, overlap).unwrap().to_json(),
        json!({"restore":NAME,"state":"open",
            "position":{"turn":3,"seq":1,"complete":true,"projections":1,"redactions":1},
            "stored_bytes":1511,"freed_bytes":0,"digest":null})
    );
    let before = snapshot(&db);
    let status = send(&reopened, request(NAME)).unwrap();
    assert_eq!(snapshot(&db), before, "probe must not refresh a restore");
    for replay in [first.clone(), second.clone()] {
        assert_eq!(send(&reopened, replay).unwrap(), status);
    }
    assert_eq!(
        rows(&db, "turn_records"),
        before[2],
        "replays rewrote payloads"
    );
    assert_eq!(
        rows(&db, "record_staging"),
        before[1],
        "replays rewrote metadata"
    );
    for old in first_rows {
        assert!(before[2].contains(&old));
    }
    let state = &rows(&db, "record_drafts")[0];
    assert_eq!(
        (
            state["owner"].as_i64(),
            state["kind"].as_str(),
            state["state"].as_str()
        ),
        (Some(owner), Some("restore"), Some("open"))
    );
    assert_eq!(state["bytes"], 5513);
    let stream: RestoreStream = serde_json::from_value(state["position"].clone()).unwrap();
    assert_eq!(stream.position(), status.position.unwrap());
    assert_eq!(
        stream.digest(),
        "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32"
    );
    let physical = db.query(
        "SELECT part, seq, last, digest, owner FROM {{schema}}.turn_records ORDER BY part, seq",
    );
    assert_eq!(physical.len(), 4);
    for (row, (turn, record)) in physical
        .iter()
        .zip(first.records.iter().chain(&second.records))
    {
        assert_eq!(
            (
                row.get::<_, i32>("part") as u64,
                row.get::<_, i32>("seq") as u64
            ),
            (*turn, record.seq)
        );
        assert_eq!(row.get::<_, i64>("owner"), owner);
        assert!(row.get::<_, bool>("last"));
        assert_eq!(row.get::<_, &[u8]>("digest"), record.digest(None));
        assert_ne!(record.digest(None), record.digest(Some(*turn)));
    }
    assert_metadata(&db, owner, &first, &second);
    assert_invisible(&db);
    let discard = Restore {
        discard: true,
        ..request(NAME)
    };
    let gone = send(&reopened, discard.clone()).unwrap();
    assert_eq!(gone.position, status.position);
    assert_eq!(
        (gone.state, gone.stored_bytes, gone.freed_bytes),
        (Staged::Discarded, 0, 5513)
    );
    let absent = send(&reopened, discard).unwrap();
    assert_eq!(
        (absent.state, absent.position, absent.freed_bytes),
        (Staged::None, None, 0)
    );
    assert!(snapshot(&db).iter().all(Vec::is_empty));
}
