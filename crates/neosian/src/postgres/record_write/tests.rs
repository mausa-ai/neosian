//! Transactional draft behavior on an operator-installed records database.
mod isolation;
mod publication;

use serde_json::json;

use crate::conversation::message;
use crate::conversation::turn_records::{State, TurnRecord, Write, Written, records_of};
use crate::conversation::{Error, Refused};
use crate::limits::Records;
use crate::postgres::PostgresStore;
use crate::postgres::records_conformance::Database;

const LIMITS: Records = Records {
    draft_seconds: 86_400,
    max_turn_bytes: 64 << 20,
    max_record_turn_bytes: 1 << 30,
};

fn data() -> Vec<TurnRecord> {
    let messages = ["one", "two", "three"]
        .map(|s| message::from_json(&json!({"role":"user","content":s})).unwrap());
    records_of(&messages).unwrap()
}
fn part(draft: &str, records: &[TurnRecord]) -> Write {
    Write {
        draft: draft.into(),
        records: records.to_vec(),
        ..Write::default()
    }
}
fn send(store: &PostgresStore, write: Write) -> Result<Written, Error> {
    store.write_records("c", write, &LIMITS)
}
fn conflict(result: Result<Written, Error>, reason: &str) -> Refused {
    let error = result.unwrap_err();
    let Error::Conversation(r) = error else {
        panic!("{}", error.text())
    };
    assert_eq!(r.code, "agent_conversation_conflict");
    assert_eq!(r.details["reason"], reason);
    r
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn partial_repeats_refusals_and_a_lost_seal_preserve_the_draft() {
    let db = Database::new();
    let store = &db.store;
    let rows = data();
    assert_eq!(send(store, part("d", &[])).unwrap().state, State::None);
    let first = send(
        store,
        Write {
            actor: Some("client:one".into()),
            turn_bytes: Some(11),
            ..part("d", &rows[..1])
        },
    )
    .unwrap();
    assert_eq!((first.state, first.next_seq), (State::Open, 1));
    assert!(store.conversations().unwrap().is_empty());
    assert_eq!(store.last_turn_number("c").unwrap(), 0);
    conflict(
        send(
            store,
            Write {
                actor: Some("client:two".into()),
                ..part("d", &rows[..1])
            },
        ),
        "draft_actor",
    );
    let gap = conflict(send(store, part("d", &rows[2..])), "draft_gap");
    assert_eq!(gap.details["next_seq"], 1);
    let mut different = rows[..1].to_vec();
    different[0].text = "ONE".into();
    let mismatch = conflict(send(store, part("d", &different)), "draft_mismatch");
    assert_eq!(mismatch.details["seq"], 0);
    conflict(
        send(
            store,
            Write {
                turn_bytes: Some(10),
                ..part("d", &[])
            },
        ),
        "draft_size",
    );
    conflict(
        send(
            store,
            Write {
                seal: true,
                ..part("d", &rows[1..2])
            },
        ),
        "draft_size",
    );
    assert_eq!(
        send(store, part("d", &[])).unwrap().next_seq,
        1,
        "new rows in a refused seal rolled back"
    );
    let continued = send(store, part("d", &rows[..2])).unwrap();
    assert_eq!(continued.next_seq, 2);
    let repeat = send(store, part("d", &rows[..2])).unwrap();
    assert_eq!((repeat.stored_bytes, repeat.freed_bytes), (0, 0));
    let sealed = send(
        store,
        Write {
            seal: true,
            ..part("d", &rows[1..])
        },
    )
    .unwrap();
    assert_eq!(sealed.turn.as_ref().unwrap().bytes, 11);
    assert_eq!(
        sealed.turn.as_ref().unwrap().actor.as_deref(),
        Some("client:one")
    );
    for retry in [
        part("d", &[]),
        Write {
            seal: true,
            ..part("d", &rows)
        },
    ] {
        let reply = send(store, retry).unwrap();
        assert_eq!(reply.turn, sealed.turn);
        assert_eq!(
            (reply.state, reply.stored_bytes, reply.freed_bytes),
            (State::Sealed, 0, 0)
        );
    }
    conflict(send(store, part("d", &different)), "draft_mismatch");
    conflict(
        send(
            store,
            Write {
                discard: true,
                ..part("d", &[])
            },
        ),
        "draft_sealed",
    );
    conflict(
        send(
            store,
            Write {
                actor: Some("other".into()),
                ..part("d", &[])
            },
        ),
        "draft_actor",
    );
    conflict(
        send(
            store,
            Write {
                turn_bytes: Some(10),
                ..part("d", &[])
            },
        ),
        "draft_size",
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_failed_open_keeps_expired_drafts_and_the_seventeenth_open_refuses() {
    let db = Database::new();
    let store = &db.store;
    let rows = data();
    let oldest = send(store, part("old", &rows[..1])).unwrap();
    for number in 0..15 {
        send(store, part(&format!("d{number}"), &rows[..1])).unwrap();
    }
    conflict(send(store, part("new", &rows[..1])), "drafts_open");
    db.execute("UPDATE {{schema}}.record_drafts SET touched_at = '2000-01-01T00:00:00Z' WHERE name = 'old'");
    let mut invalid = rows[..1].to_vec();
    invalid[0].message = 5;
    assert!(send(store, part("new", &invalid)).is_err());
    assert_eq!(
        db.query("SELECT owner FROM {{schema}}.record_drafts WHERE name='old'")
            .len(),
        1
    );
    let new = send(store, part("new", &rows[..1])).unwrap();
    assert_eq!(new.freed_bytes, oldest.stored_bytes);
    assert_eq!(send(store, part("old", &[])).unwrap().state, State::None);
    let actual =
        db.query("SELECT count(*) AS n FROM {{schema}}.turn_records")[0].get::<_, i64>("n");
    assert_eq!(actual, 16, "the stale owner's rows are gone");
    let gone = send(
        store,
        Write {
            discard: true,
            ..part("new", &[])
        },
    )
    .unwrap();
    assert_eq!(
        (gone.state, gone.next_seq, gone.freed_bytes),
        (State::Discarded, 1, new.stored_bytes)
    );
    assert_eq!(send(store, part("new", &[])).unwrap().state, State::None);
    let smaller = Records {
        max_record_turn_bytes: 1,
        ..LIMITS
    };
    let seal = Write {
        seal: true,
        ..part("d0", &[])
    };
    let error = store.write_records("c", seal, &smaller).unwrap_err().text();
    assert!(error.contains("NEOSIAN_MAX_RECORD_TURN_BYTES"), "{error}");
    assert_eq!(send(store, part("d0", &[])).unwrap().state, State::Open);
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn simultaneous_appends_and_seals_share_one_gapless_sequence() {
    use std::sync::Barrier;
    let db = Database::new();
    let rows = data();
    const WRITERS: usize = 12;
    for n in 0..WRITERS / 2 {
        send(&db.store, part(&format!("d{n}"), &rows[..1])).unwrap();
    }
    // Delay each actual insert after its MAX(turn) was read; independent clients
    // regularly choose the same number on the first attempt.
    db.execute("CREATE FUNCTION {{schema}}.delay_turn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.015); RETURN NEW; END $$");
    db.execute("CREATE TRIGGER delayed BEFORE INSERT ON {{schema}}.turns FOR EACH ROW EXECUTE FUNCTION {{schema}}.delay_turn()");
    let stores: Vec<_> = (0..WRITERS).map(|_| db.another_store()).collect();
    let start = Barrier::new(WRITERS);
    let mut numbers = std::thread::scope(|scope| {
        let jobs: Vec<_> = stores
            .iter()
            .enumerate()
            .map(|(n, store)| {
                let start = &start;
                scope.spawn(move || {
                    start.wait();
                    if n < WRITERS / 2 {
                        send(
                            store,
                            Write {
                                seal: true,
                                ..part(&format!("d{n}"), &[])
                            },
                        )
                        .unwrap()
                        .turn
                        .unwrap()
                        .turn
                    } else {
                        let msg =
                            message::from_json(&json!({"role":"user","content":"legacy"})).unwrap();
                        store.append_turn("c", vec![msg], None).unwrap().turn
                    }
                })
            })
            .collect();
        jobs.into_iter()
            .map(|j| j.join().unwrap())
            .collect::<Vec<_>>()
    });
    numbers.sort_unstable();
    assert_eq!(numbers, (1..=WRITERS as u64).collect::<Vec<_>>());
}
