//! A staged restore holds no lock between requests. When a client publishes,
//! ordinary schema-4 writers on the same server compete for its conversation.
use super::{Database, PostgresStore};
use crate::conversation::turn_records::{Header, Restore, Staged, records_of};
use crate::conversation::{Error, Projection, Turn, message};
use crate::limits::Records;
use crate::store::time::Stamp;
use serde_json::json;
use std::sync::mpsc;
use std::time::{Duration, Instant};

const LIMITS: Records = Records {
    draft_seconds: 3600,
    max_turn_bytes: 64 << 20,
    max_record_turn_bytes: 1 << 30,
};

#[derive(Clone, Copy, Debug)]
enum Ordinary {
    Whole,
    ProjectionsOnly,
    AppendTurn,
    AppendProjections,
}

fn prompt(text: &str) -> message::Message {
    message::from_json(&json!({"role":"user","content":text})).unwrap()
}

impl Ordinary {
    fn apply(self, store: &PostgresStore, id: &str) -> Result<(), Error> {
        let projection = Projection {
            turn: 13,
            span: 1,
            kind: "log".into(),
            text: "legacy".into(),
        };
        match self {
            Self::Whole => {
                let turn = Turn {
                    conversation_id: id.into(),
                    turn: 13,
                    messages: vec![prompt("legacy")],
                    created_at: Stamp::now(),
                    actor: None,
                    redacted: false,
                };
                store.restore_conversation(id, &[turn], &[], &[])
            }
            Self::ProjectionsOnly => store.restore_conversation(id, &[], &[projection], &[]),
            Self::AppendTurn => store
                .append_turn(id, vec![prompt("legacy")], None)
                .map(|_| ()),
            Self::AppendProjections => store.append_projections(id, &[projection]),
        }
    }
}

fn probe() -> Restore {
    Restore {
        restore: "staged".into(),
        ..Restore::default()
    }
}

fn commit(store: &PostgresStore, id: &str) -> Result<(), Error> {
    let result = store.restore_records(
        id,
        Restore {
            commit: true,
            ..probe()
        },
        &LIMITS,
    )?;
    assert_eq!(result.state, Staged::Committed);
    Ok(())
}

fn occupied(result: Result<(), Error>) {
    let Err(Error::Conversation(refusal)) = result else {
        panic!("expected target_occupied, got {result:?}");
    };
    assert_eq!(refusal.code, "agent_conversation_conflict");
    assert_eq!(refusal.details["reason"], "target_occupied");
}

// Wait for actual database sessions queued on the specific advisory lock.
// On timeout the caller releases the lock before joining any worker thread.
fn wait_for(db: &Database, wanted: i64) -> bool {
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        let waiting: i64 = db.query(
            "SELECT count(*) FROM pg_locks held JOIN pg_locks waiting
             USING (locktype,database,classid,objid,objsubid)
             WHERE held.pid = pg_backend_pid() AND held.locktype = 'advisory'
               AND held.granted AND NOT waiting.granted",
        )[0]
        .get(0);
        if waiting == wanted {
            return true;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    false
}

fn race(db: &Database, other: &PostgresStore, op: Ordinary, commit_first: bool, id: &str) {
    let records = records_of(&[prompt("staged")]).unwrap();
    let part = Restore {
        turns: vec![Header::of(7, Stamp::now(), None, &records)],
        records: records.into_iter().map(|row| (7, row)).collect(),
        ..probe()
    };
    let opened = db.store.restore_records(id, part, &LIMITS).unwrap();
    let key = format!("\"{}\":records:{id}", db.schema);
    db.execute(&format!(
        "SELECT pg_advisory_lock(hashtextextended('{key}',0))"
    ));

    let run = |publish: bool| {
        if publish {
            commit(&db.store, id)
        } else {
            op.apply(other, id)
        }
    };
    let (first, second) = std::thread::scope(|scope| {
        let first = scope.spawn(|| run(commit_first));
        let first_waited = wait_for(db, 1);
        let second = scope.spawn(|| run(!commit_first));
        let both_waited = wait_for(db, 2);
        let (send, recv) = mpsc::channel();
        let unrelated = format!("{id}-elsewhere");
        scope.spawn(move || {
            send.send(Ordinary::AppendTurn.apply(other, &unrelated))
                .unwrap();
        });
        let independent = recv.recv_timeout(Duration::from_secs(4));
        db.execute("SELECT pg_advisory_unlock_all()");
        assert!(
            first_waited && both_waited,
            "both writers queued in order: {op:?}, publish first = {commit_first}"
        );
        independent
            .expect("an unrelated conversation must remain writable")
            .unwrap();
        (first.join().unwrap(), second.join().unwrap())
    });

    first.unwrap();
    let read = || {
        db.store
            .read_turns(id, 0, None)
            .unwrap()
            .into_iter()
            .map(|turn| turn.turn)
            .collect::<Vec<_>>()
    };
    let projections = || db.store.read_projections(id, 0, None).unwrap().len();
    if commit_first {
        match op {
            Ordinary::Whole | Ordinary::ProjectionsOnly => {
                occupied(second);
                assert_eq!(read(), [7]);
                assert_eq!(projections(), 0);
            }
            Ordinary::AppendTurn => {
                second.unwrap();
                assert_eq!(read(), [7, 8]);
            }
            Ordinary::AppendProjections => {
                second.unwrap();
                assert_eq!(read(), [7]);
                assert_eq!(projections(), 1);
            }
        }
    } else {
        occupied(second);
        let status = db.store.restore_records(id, probe(), &LIMITS).unwrap();
        assert_eq!(
            (status.state, status.position),
            (Staged::Open, opened.position)
        );
        match op {
            Ordinary::Whole => assert_eq!(read(), [13]),
            Ordinary::AppendTurn => assert_eq!(read(), [1]),
            _ => {
                assert!(read().is_empty());
                assert_eq!(projections(), 1);
            }
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn both_lock_orders_use_fresh_occupancy_for_ordinary_writers() {
    let db = Database::new();
    let other = db.another_store();
    db.store.pool.set_test_repeatable_read().unwrap();
    other.pool.set_test_repeatable_read().unwrap();
    for (i, op) in [
        Ordinary::Whole,
        Ordinary::ProjectionsOnly,
        Ordinary::AppendTurn,
        Ordinary::AppendProjections,
    ]
    .into_iter()
    .enumerate()
    {
        for first in [false, true] {
            race(&db, &other, op, first, &format!("race-{i}-{first}"));
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn ordinary_writes_keep_their_exact_response_row_budgets() {
    const CASE: &str = "NEOSIAN_TEST_CONVERSATION_WRITE_BUDGET";
    let Ok(budget) = std::env::var(CASE) else {
        for budget in ["1", "9", "10", "13", "14"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    std::thread::current().name().unwrap(),
                    "--ignored",
                ])
                .env(CASE, budget)
                .env("NEOSIAN_MAX_RESPONSE_BYTES", budget)
                .output()
                .unwrap();
            assert!(
                output.status.success(),
                "budget {budget}: {}",
                String::from_utf8_lossy(&output.stdout)
            );
        }
        return;
    };
    let budget: u64 = budget.parse().unwrap();
    for installed in [false, true] {
        let db = Database::new();
        if !installed {
            db.execute("DROP TABLE {{schema}}.record_staging, {{schema}}.record_turns, {{schema}}.turn_records, {{schema}}.record_drafts, {{schema}}.neosian_records");
        }
        check_budget(&db, budget);
    }
    if budget == 1 {
        let db = Database::new();
        for (change, refusal) in [
            (
                "UPDATE {{schema}}.neosian_records SET version=2",
                "incomplete",
            ),
            (
                "UPDATE {{schema}}.neosian_records SET version=3",
                "unsupported",
            ),
            (
                "ALTER TABLE {{schema}}.neosian_records RENAME version TO missing",
                "incomplete",
            ),
            ("DROP TABLE {{schema}}.turn_records", "incomplete"),
        ] {
            db.execute(change);
            let error = Ordinary::AppendProjections
                .apply(&db.another_store(), "invalid")
                .unwrap_err();
            assert!(
                error
                    .text()
                    .contains(&format!("{refusal} Postgres records extension"))
            );
            assert_eq!(
                db.query("SELECT count(*) FROM {{schema}}.projections")[0].get::<_, i64>(0),
                0
            );
        }
    }
}

fn check_budget(db: &Database, budget: u64) {
    for (op, expected, id) in [
        (Ordinary::AppendProjections, 0_u64, "projection"),
        (Ordinary::AppendTurn, 10, "append"), // {"turn":1}
        (Ordinary::Whole, 14, "whole"),       // {"empty":true}
    ] {
        let fresh = db.another_store();
        let result = op.apply(&fresh, id);
        if expected <= budget {
            result.unwrap();
        } else {
            let message = result.unwrap_err().text();
            assert!(
                message.contains(&format!(
                    "NEOSIAN_MAX_RESPONSE_BYTES exceeded: {expected} > {budget};"
                )),
                "{message}"
            );
        }
    }
    // Under autocommit, a diagnostic row that exceeds the budget is a read
    // refusal. The mutation that produced it has already taken effect.
    assert_eq!(
        db.query("SELECT count(*) FROM {{schema}}.turns")[0].get::<_, i64>(0),
        2
    );
    assert_eq!(
        db.query("SELECT count(*) FROM {{schema}}.projections")[0].get::<_, i64>(0),
        1
    );
}
