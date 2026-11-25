//! A controller queues two independent SQL sessions in both orders so the
//! loser must observe the winner's publication or erasure.
use super::*;
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug)]
enum Concurrent {
    Publish,
    Seal,
    Append,
}

fn waiting(db: &Database, n: i64) -> bool {
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        let rows = db.query(
            "SELECT count(*) FROM pg_locks holder JOIN pg_locks waiter
            USING(locktype,database,classid,objid,objsubid)
            WHERE holder.pid=pg_backend_pid() AND holder.locktype='advisory'
              AND holder.granted AND NOT waiter.granted",
        );
        if rows[0].get::<_, i64>(0) == n {
            return true;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    false
}

fn race(db: &Database, id: &str, other: Concurrent, erase_first: bool) {
    let store = &db.store;
    match other {
        Concurrent::Publish => {
            store
                .restore_records(id, restore_part(4, "first", false), &LIMITS)
                .unwrap();
        }
        Concurrent::Seal => write(store, id, "open", "before seal", false),
        Concurrent::Append => {
            store
                .append_turn(id, vec![message("user", "first")], None)
                .unwrap();
        }
    }
    let invoke = |erase| -> Result<usize, Error> {
        if erase {
            return store.redact_turns(id, None, None, Some("erase"));
        }
        match other {
            Concurrent::Publish => {
                store.restore_records(id, restore_part(11, "last", true), &LIMITS)?;
            }
            Concurrent::Seal => {
                store.write_records(
                    id,
                    Write {
                        draft: "open".into(),
                        seal: true,
                        ..Write::default()
                    },
                    &LIMITS,
                )?;
            }
            Concurrent::Append => {
                store.append_turn(id, vec![message("user", "second")], None)?;
            }
        }
        Ok(0)
    };
    let key = format!("\"{}\":records:{id}", db.schema);
    db.execute(&format!(
        "SELECT pg_advisory_lock(hashtextextended('{key}',0))"
    ));
    let (first, second) = std::thread::scope(|threads| {
        let a = threads.spawn(|| invoke(erase_first));
        let first_waited = waiting(db, 1);
        let b = threads.spawn(|| invoke(!erase_first));
        let both_waited = waiting(db, 2);
        db.execute("SELECT pg_advisory_unlock_all()");
        let result = (a.join().unwrap(), b.join().unwrap());
        assert!(
            first_waited && both_waited,
            "{other:?}, erase first={erase_first}: both sessions must wait on the conversation"
        );
        result
    });
    let (erasure, writer) = if erase_first {
        (first, second)
    } else {
        (second, first)
    };
    let count = erasure.unwrap();
    let actual = store
        .read_turns(id, 0, None)
        .unwrap()
        .into_iter()
        .map(|t| (t.turn, t.redacted))
        .collect::<Vec<_>>();
    let expected: &[(u64, bool)] = match (other, erase_first) {
        // A data-bearing final part may open a new sparse unit on an empty
        // target; it must not resurrect the first, discarded restore part.
        (Concurrent::Publish, true) => &[(11, false)],
        (Concurrent::Publish, false) => &[(4, true), (11, true)],
        (Concurrent::Seal, true) => &[],
        (Concurrent::Seal, false) => &[(1, true)],
        (Concurrent::Append, true) => &[(1, true), (2, false)],
        (Concurrent::Append, false) => &[(1, true), (2, true)],
    };
    assert_eq!(actual, expected);
    if matches!(other, Concurrent::Seal) && erase_first {
        assert!(
            matches!(writer,Err(Error::Value(why)) if why.contains("seal needs at least one record"))
        );
    } else {
        writer.unwrap();
    }
    let redacted: Vec<_> = expected
        .iter()
        .filter(|(_, erased)| *erased)
        .map(|(n, _)| *n)
        .collect();
    assert_eq!(count, redacted.len());
    let trail = store.turn_redactions(Some(&[id.into()]), None, 10).unwrap();
    assert_eq!(trail.len(), usize::from(count > 0));
    if count > 0 {
        assert_eq!(trail[0].turns, redacted);
    }
    if erase_first && matches!(other, Concurrent::Publish) {
        assert_eq!(belonging(db, "record_turns", id).len(), 1);
        let records = belonging(db, "turn_records", id);
        assert_eq!(records.len(), 2);
        assert!(records.iter().all(|r| r["part"] == 11));
    } else {
        for table in [
            "record_turns",
            "turn_records",
            "record_drafts",
            "record_staging",
        ] {
            assert!(belonging(db, table, id).is_empty(), "{table}");
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn erase_and_writers_recheck_state_after_the_same_conversation_lock() {
    let db = Database::new();
    db.store.pool.set_test_repeatable_read().unwrap();
    for (i, op) in [Concurrent::Publish, Concurrent::Seal, Concurrent::Append]
        .into_iter()
        .enumerate()
    {
        for erase_first in [false, true] {
            race(&db, &format!("race-{i}-{erase_first}"), op, erase_first);
        }
    }
}
