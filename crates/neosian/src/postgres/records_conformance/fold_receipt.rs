//! Original native seal retries on an explicitly upgraded PostgreSQL records store.
mod erase;
mod lifecycle;

use super::Database;
use super::restore_stage::{ID, LIMITS, NAME, example, message};
use crate::conversation::Error;
use crate::conversation::turn_records::migration::migration_id;
use crate::conversation::turn_records::{
    Header, Staged, State, TurnRecord, Write, Written, records_of,
};
use crate::postgres::PostgresStore;

fn fixture() -> Database {
    let db = Database::new();
    db.execute(crate::postgres::sql!("records-postgres-v2"));
    db
}
fn pieces(text: &str, chunk_bytes: usize) -> Vec<TurnRecord> {
    let all = records_of(&[message("user", text)]).unwrap().remove(0);
    text.as_bytes()
        .chunks(chunk_bytes)
        .enumerate()
        .map(|(i, chunk)| TurnRecord {
            seq: i as u64,
            chunk: i as u64,
            content: if i == 0 { all.content } else { None },
            text: std::str::from_utf8(chunk).unwrap().into(),
            ..all.clone()
        })
        .collect()
}
fn part(draft: &str, data: &[TurnRecord]) -> Write {
    Write {
        draft: draft.into(),
        records: data.to_vec(),
        ..Default::default()
    }
}
fn send(s: &PostgresStore, id: &str, write: Write) -> Result<Written, Error> {
    s.write_records(id, write, &LIMITS)
}
fn seal(s: &PostgresStore, id: &str, draft: &str, data: &[TurnRecord]) -> Header {
    let size = data.iter().map(|r| r.text.len() as u64).sum();
    send(
        s,
        id,
        Write {
            actor: Some("agent:a".into()),
            turn_bytes: Some(size),
            seal: true,
            ..part(draft, data)
        },
    )
    .unwrap()
    .turn
    .unwrap()
}
fn refusal(e: Error, reason: &str) -> serde_json::Value {
    let Error::Conversation(r) = e else {
        panic!("{}", e.text())
    };
    assert_eq!(r.code, "agent_conversation_conflict");
    assert_eq!(r.details["reason"], reason);
    r.details
}
fn check(s: &PostgresStore, id: &str, name: &str, data: &[TurnRecord], h: &Header) {
    for subset in [&data[..0], &data[..1], &data[1..], data] {
        for sealed in [false, true] {
            let r = send(
                s,
                id,
                Write {
                    seal: sealed,
                    ..part(name, subset)
                },
            )
            .unwrap();
            assert_eq!(
                (r.state, r.next_seq, r.stored_bytes, r.freed_bytes),
                (State::Sealed, h.records, 0, 0)
            );
            assert_eq!(r.turn.as_ref(), Some(h));
        }
    }
    let mut changed = data[1..].to_vec();
    changed[0].text = "wrong!".into();
    assert_eq!(
        refusal(
            send(s, id, part(name, &changed)).unwrap_err(),
            "draft_mismatch"
        )["seq"],
        1
    );
    let removed = Write {
        discard: true,
        ..part(name, &[])
    };
    assert_eq!(
        refusal(send(s, id, removed).unwrap_err(), "draft_sealed")["turn"],
        h.to_json()
    );
    for seq in [h.records, h.records + 10] {
        let beyond = TurnRecord {
            seq,
            ..data[0].clone()
        };
        assert_eq!(
            refusal(
                send(s, id, part(name, &[beyond])).unwrap_err(),
                "draft_sealed"
            )["turn"],
            h.to_json()
        );
    }
    refusal(
        send(
            s,
            id,
            Write {
                actor: Some("agent:b".into()),
                ..part(name, &[])
            },
        )
        .unwrap_err(),
        "draft_actor",
    );
    assert_eq!(
        refusal(
            send(
                s,
                id,
                Write {
                    turn_bytes: Some(h.bytes + 1),
                    ..part(name, &[])
                }
            )
            .unwrap_err(),
            "draft_size"
        )["bytes"],
        h.bytes
    );
}
fn snapshot(db: &Database) -> Vec<serde_json::Value> {
    db.query("SELECT to_jsonb(x) AS row FROM (SELECT 'h' AS k, to_jsonb(h) AS value, ctid::text, xmin::text FROM {{schema}}.record_seals h
      UNION ALL SELECT 'p',to_jsonb(p), ctid::text,xmin::text FROM {{schema}}.record_seal_pages p) x ORDER BY k,value").iter().map(|r|r.get(0)).collect()
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn cold_replay_reads_original_cross_page_ranges_and_names_incomplete_receipt() {
    let db = fixture();
    let data = pieces(&"x".repeat(99), 1);
    let head = seal(&db.store, ID, "long", &data);
    db.store.fold_records(ID, false, &LIMITS).unwrap();
    for (lo, hi) in [(0, 1), (31, 33), (63, 97), (98, 99), (0, 99)] {
        let r = send(&db.another_store(), ID, part("long", &data[lo..hi])).unwrap();
        assert_eq!(r.turn.as_ref(), Some(&head));
        println!(
            "range [{lo},{hi}) confirmed across reopen; {} digest bytes requested",
            32 * (hi - lo)
        );
    }
    db.execute("DELETE FROM {{schema}}.record_seal_pages WHERE page=1");
    let why = send(&db.another_store(), ID, part("long", &data[..1]))
        .unwrap_err()
        .text();
    assert!(
        why.contains("turn 1: its folded receipt is unreadable"),
        "{why}"
    );
    assert_eq!(
        db.query("SELECT pages_seen FROM {{schema}}.record_seals")[0].get::<_, i32>(0),
        3
    );
    println!("missing middle page detected by first-digest retry; no full digest scan");
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn open_gap_remains_an_open_gap_and_retention_failures_rollback() {
    let db = fixture();
    let data = pieces(&"x".repeat(67), 1);
    let req = Write {
        actor: Some("agent:a".into()),
        turn_bytes: Some(67),
        ..part("long", &data[..1])
    };
    assert_eq!(send(&db.store, ID, req).unwrap().state, State::Open);
    assert_eq!(
        refusal(
            send(&db.store, ID, part("long", &data[2..3])).unwrap_err(),
            "draft_gap"
        )["next_seq"],
        1
    );
    let h = seal(&db.store, ID, "long", &data);
    db.execute("CREATE FUNCTION {{schema}}.skip_receipt_page() RETURNS trigger LANGUAGE plpgsql AS $x$ BEGIN IF NEW.page=1 THEN RETURN NULL; END IF; RETURN NEW; END $x$;
        CREATE TRIGGER drop_one BEFORE INSERT ON {{schema}}.record_seal_pages FOR EACH ROW EXECUTE FUNCTION {{schema}}.skip_receipt_page()");
    let why = db
        .store
        .fold_records(ID, false, &LIMITS)
        .unwrap_err()
        .text();
    assert!(
        why.contains("retain its exact native seal evidence"),
        "{why}"
    );
    assert!(snapshot(&db).is_empty());
    check(&db.another_store(), ID, "long", &data, &h);
    db.execute("DROP TRIGGER drop_one ON {{schema}}.record_seal_pages");
    assert_eq!(
        db.another_store()
            .fold_records(ID, false, &LIMITS)
            .unwrap()
            .folded,
        1
    );
    check(&db.another_store(), ID, "long", &data, &h);
    println!(
        "open-gap, suppressed middle page, rollback, cold retry and exact confirmation verified"
    );
}
