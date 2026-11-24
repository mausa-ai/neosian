//! The Postgres record reader uses the same views and page boundaries as a
//! FileStore holding the same messages. Live cases require a disposable DSN.
mod heads;
mod legacy;

use super::*;
use crate::conversation::page::Mode;
use crate::conversation::turn_records::{
    Kind, ReadItem, ReadPosition, RecordRead, Write, records_of,
};
use crate::postgres::records_conformance::Database;
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;
use serde_json::json;

const ID: &str = "pg-read";
const TEST_TIME: &str = "2026-11-22T10:30:00.954321Z";

fn messages(data: &[Value]) -> Vec<message::Message> {
    data.iter()
        .map(|m| message::from_json(m).unwrap())
        .collect()
}

fn populate(db: &PostgresStore) -> Vec<Value> {
    let seed = |turn, redacted| Turn {
        conversation_id: ID.into(),
        turn,
        created_at: Stamp::parse(TEST_TIME).unwrap(),
        actor: Some("cli:archive".into()),
        redacted,
        messages: if redacted {
            Vec::new()
        } else {
            messages(&[json!({"role":"user","content":"legacy before"})])
        },
    };
    db.restore_conversation(ID, &[seed(1, false), seed(2, true)], &[], &[])
        .unwrap();
    let body = vec![
        json!({"role":"user","content":"report"}),
        json!({"role":"assistant","content":null,"tool_calls":[{"id":"c1","name":"read","arguments":{"text":"é"}}]}),
        json!({"role":"tool","tool_call_id":"c1","content":format!("first {} last", "かな\n".repeat(20_000))}),
        json!({"role":"assistant","content":"done"}),
    ];
    let records = records_of(&messages(&body)).unwrap();
    assert!(records.len() > 5);
    let write = Write {
        draft: "read-draft".into(),
        records,
        seal: true,
        actor: Some("cli:archive".into()),
        ..Write::default()
    };
    db.write_records(ID, write, &limits::Records::load().unwrap())
        .unwrap();
    db.append_turn(
        ID,
        messages(&[json!({"role":"user","content":"legacy after"})]),
        None,
    )
    .unwrap();
    body
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn views_pages_and_legacy_turns_match_the_filestore() {
    let db = Database::new();
    let expected = populate(&db.store);
    let turns = db.store.read_turns(ID, 0, None).unwrap();
    assert_eq!(
        turns.iter().map(|t| t.turn).collect::<Vec<_>>(),
        [1, 2, 3, 4]
    );
    let want = messages(&expected);
    assert_eq!(
        turns[2]
            .messages
            .iter()
            .map(message::to_json)
            .collect::<Vec<_>>(),
        want.iter().map(message::to_json).collect::<Vec<_>>()
    );
    let home = Scratch::new("pg-file-read-diff");
    let files = FileStore::open(home.path()).unwrap();
    files.enable_records().unwrap();
    files.restore_conversation(ID, &turns, &[], &[]).unwrap();
    // The next seq falls inside a long tool piece. Through both forms of
    // continuation the header repeats and every selected record agrees.
    let mut requests = vec![
        RecordRead::default(),
        RecordRead {
            kinds: Some(Vec::new()),
            ..Default::default()
        },
        RecordRead {
            kinds: Some(vec![Kind::Prompt, Kind::Reply]),
            ..Default::default()
        },
        RecordRead {
            heads: vec![(Kind::ToolResult, 26)],
            ..Default::default()
        },
        RecordRead {
            turn: Some(3),
            seq: Some(3),
            ..Default::default()
        },
        RecordRead {
            turn: Some(3),
            seq: Some(3),
            piece: true,
            heads: vec![(Kind::ToolResult, 3)],
            ..Default::default()
        },
        RecordRead {
            after: 1,
            through: Some(3),
            ..Default::default()
        },
    ];
    requests.push(RecordRead {
        turn: Some(3),
        seq: Some(u64::MAX),
        piece: true,
        ..Default::default()
    });
    for request in requests {
        for bytes in [1, 65_536, 983_040] {
            let mut r = RecordRead {
                page_bytes: bytes,
                ..request.clone()
            };
            let mut pages = 0;
            loop {
                let mut a = vec![];
                let mut b = vec![];
                let file = files
                    .read_records(ID, &r, &mut |item| {
                        a.push(item);
                        Ok(())
                    })
                    .unwrap();
                let pg = db
                    .store
                    .read_records(ID, &r, &mut |item| {
                        b.push(item);
                        Ok(())
                    })
                    .unwrap();
                assert_eq!(a, b, "{r:?}");
                let key = |position: ReadPosition| (position.turn, position.seq);
                assert_eq!(file.map(key), pg.map(key));
                let Some(next) = pg else { break };
                r.continuation = Some(next.encode(&r.fingerprint(ID)));
                pages += 1;
                assert!(pages < 20);
            }
        }
    }
    for mode in [Mode::Each, Mode::Paged(128)] {
        let mut seen = Vec::new();
        db.store
            .read_turns_in(ID, 1, Some(2), mode, &mut |turn| {
                seen.push(turn.turn);
                Ok(true)
            })
            .unwrap();
        assert_eq!(seen, [2, 3]);
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn legacy_refuses_over_the_bound_before_loading_any_record() {
    let db = Database::new();
    let source = populate(&db.store);
    let bound = limits::Records::load().unwrap().max_turn_bytes;
    let actual: u64 = records_of(&messages(&source))
        .unwrap()
        .iter()
        .map(|record| record.text.len() as u64)
        .sum();
    let original = db.store.read_turns(ID, 2, Some(1));
    if actual > bound {
        assert_eq!(
            original.expect_err("O11 for a real record turn").text(),
            limits::turn_too_long(3, ID, actual, bound)
        );
    } else {
        assert_eq!(original.unwrap()[0].turn, 3);
    }
    // O11 must be decided by the mapping header, even when the rows could
    // not be read. A legacy reader must not return the empty SQL placeholder.
    let size = bound + 1;
    db.execute(&format!(
        "UPDATE {{{{schema}}}}.record_turns SET bytes={size} WHERE turn=3;
         DELETE FROM {{{{schema}}}}.turn_records"
    ));
    for mode in [Mode::Each, Mode::Paged(4000)] {
        let refusal = db
            .store
            .read_turns_in(ID, 2, Some(1), mode, &mut |_| panic!("unexpected turn"))
            .unwrap_err();
        assert_eq!(
            refusal.text(),
            limits::turn_too_long(3, ID, bound + 1, bound)
        );
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_page_uses_one_snapshot_for_headers_and_records() {
    let db = Database::new();
    populate(&db.store);
    let mut changed = false;
    let mut numbers = Vec::new();
    db.store
        .read_records(ID, &RecordRead::default(), &mut |item| {
            if let ReadItem::Record { turn, record, .. } = item {
                if !changed {
                    std::thread::scope(|s| {
                        s.spawn(|| db.execute("DELETE FROM {{schema}}.turn_records"))
                            .join()
                            .unwrap()
                    });
                    changed = true;
                }
                if turn == 3 {
                    numbers.push(record.seq);
                }
            }
            Ok(())
        })
        .unwrap();
    assert!(changed && numbers.len() > 5);
    assert_eq!(numbers, (0..numbers.len() as u64).collect::<Vec<_>>());
    let mut headers = Vec::new();
    db.store
        .read_records(
            ID,
            &RecordRead {
                kinds: Some(vec![]),
                ..Default::default()
            },
            &mut |item| {
                let ReadItem::Header(header) = item else {
                    panic!("header-only selection returned a record")
                };
                headers.push(header.turn);
                Ok(())
            },
        )
        .unwrap();
    assert_eq!(headers, [1, 2, 3, 4]);
    let mut discard = |_| Ok(());
    assert!(
        db.store
            .read_records(
                ID,
                &RecordRead {
                    turn: Some(3),
                    ..Default::default()
                },
                &mut discard
            )
            .is_err()
    );
}
