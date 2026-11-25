use super::*;
use std::time::{Duration, Instant};

fn waiters(db: &Database, wanted: i64) -> bool {
    let until = Instant::now() + Duration::from_secs(5);
    while Instant::now() < until {
        let n: i64 = db.query(
            "SELECT count(*) FROM pg_locks holder JOIN pg_locks waiter
             USING(locktype,database,classid,objid,objsubid)
             WHERE holder.pid=pg_backend_pid() AND holder.locktype='advisory'
               AND holder.granted AND NOT waiter.granted",
        )[0]
        .get(0);
        if n == wanted {
            return true;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    false
}

// Both sessions have completed any candidate discovery when we observe them
// queued in order at the same conversation lock.
fn in_order<T: Send, U: Send>(
    db: &Database,
    first: impl FnOnce() -> T + Send,
    second: impl FnOnce() -> U + Send,
) -> (T, U) {
    db.execute("SELECT pg_advisory_lock(hashtextextended('{{schema}}:records:a',0))");
    std::thread::scope(|scope| {
        let a = scope.spawn(first);
        let first_waited = waiters(db, 1);
        let b = scope.spawn(second);
        let both_waited = waiters(db, 2);
        db.execute("SELECT pg_advisory_unlock_all()");
        let result = (a.join().unwrap(), b.join().unwrap());
        assert!(
            first_waited && both_waited,
            "both operations must queue at conversation a"
        );
        result
    })
}

#[derive(Clone, Copy)]
enum Winner {
    Refresh,
    Seal,
    Publish,
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn discovery_rechecks_refreshed_sealed_and_published_owners_and_refills_the_page() {
    for winner in [Winner::Refresh, Winner::Seal, Winner::Publish] {
        let db = Database::new();
        db.store.pool.set_test_repeatable_read().unwrap();
        if matches!(winner, Winner::Publish) {
            restore(&db, "a", "shared", false);
        } else {
            write(&db, "a", "shared", false);
        }
        let b = write(&db, "b", "shared", false);
        let c = write(&db, "c", "shared", false);
        stale(&db, "true");
        let update = || match winner {
            Winner::Refresh | Winner::Seal => {
                let w = db
                    .store
                    .write_records(
                        "a",
                        Write {
                            draft: "shared".into(),
                            // A probe is not a touch: the refresh repeats
                            // the draft's record.
                            records: match winner {
                                Winner::Refresh => {
                                    records_of(&[message("user", "some text")]).unwrap()
                                }
                                _ => Vec::new(),
                            },
                            seal: matches!(winner, Winner::Seal),
                            ..Default::default()
                        },
                        &LIMITS,
                    )
                    .unwrap();
                assert_eq!(
                    w.state.as_str(),
                    if matches!(winner, Winner::Seal) {
                        "sealed"
                    } else {
                        "open"
                    }
                );
            }
            Winner::Publish => {
                let r = db
                    .store
                    .restore_records(
                        "a",
                        Restore {
                            restore: "shared".into(),
                            commit: true,
                            ..Default::default()
                        },
                        &LIMITS,
                    )
                    .unwrap();
                assert_eq!(r.state.as_str(), "committed");
            }
        };
        let (_, page) = in_order(&db, update, || {
            db.store.sweep_records(1, None, true).unwrap()
        });
        assert_eq!(
            serde_json::to_value(page.conversations).unwrap(),
            json!([group("b", 1, 0, b)])
        );
        let cursor = page.next_cursor.expect("c is still stale");
        let tail = db.store.sweep_records(1, Some(&cursor), true).unwrap();
        assert_eq!(
            serde_json::to_value(tail.conversations).unwrap(),
            json!([group("c", 1, 0, c)])
        );
        assert!(tail.next_cursor.is_none());
        let visible = db.store.read_turns("a", 0, None).unwrap();
        match winner {
            Winner::Refresh => {
                assert!(visible.is_empty());
                let check = db
                    .store
                    .write_records(
                        "a",
                        Write {
                            draft: "shared".into(),
                            ..Default::default()
                        },
                        &LIMITS,
                    )
                    .unwrap();
                assert_eq!((check.state.as_str(), check.next_seq), ("open", 1));
            }
            Winner::Seal => assert_eq!(visible.iter().map(|t| t.turn).collect::<Vec<_>>(), [1]),
            Winner::Publish => assert_eq!(visible.iter().map(|t| t.turn).collect::<Vec<_>>(), [4]),
        }
        assert!(
            db.store
                .sweep_records(1, None, true)
                .unwrap()
                .conversations
                .is_empty()
        );
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_second_sweeper_or_seal_cannot_reclaim_the_first_sweepers_refund() {
    for second_sweeps in [true, false] {
        let db = Database::new();
        db.store.pool.set_test_repeatable_read().unwrap();
        let a = write(&db, "a", "shared", false);
        let b = write(&db, "b", "shared", false);
        stale(&db, "true");
        let apply = || db.store.sweep_records(1, None, true).unwrap();
        let (first, second) = in_order(&db, apply, || {
            if second_sweeps {
                return Some(apply());
            }
            let err = db
                .store
                .write_records(
                    "a",
                    Write {
                        draft: "shared".into(),
                        seal: true,
                        ..Default::default()
                    },
                    &LIMITS,
                )
                .unwrap_err();
            assert!(
                matches!(err,crate::conversation::Error::Value(msg) if msg.contains("a seal needs at least one record"))
            );
            None
        });
        assert_eq!(
            serde_json::to_value(first.conversations).unwrap(),
            json!([group("a", 1, 0, a)])
        );
        // A parallel sweeper may have removed b before the first looks ahead.
        let second = second.unwrap_or_else(apply);
        assert_eq!(
            serde_json::to_value(second.conversations).unwrap(),
            json!([group("b", 1, 0, b)])
        );
        assert!(second.next_cursor.is_none());
        assert!(apply().conversations.is_empty());
        assert!(db.store.read_turns("a", 0, None).unwrap().is_empty());
    }
}
