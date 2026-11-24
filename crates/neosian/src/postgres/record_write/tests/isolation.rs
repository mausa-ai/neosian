//! A database default may pin snapshots before the advisory lock is granted.
use super::*;
use std::time::{Duration, Instant};

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn two_waiting_opens_recheck_the_last_slot_after_their_conversation_lock() {
    let db = Database::new();
    let rows = data();
    for n in 0..15 {
        send(&db.store, part(&format!("held{n}"), &rows[..1])).unwrap();
    }
    let competing = db.another_store();
    competing.pool.set_test_repeatable_read().unwrap();
    let key = format!("\"{}\":records:c", db.schema);
    db.execute(&format!(
        "SELECT pg_advisory_lock(hashtextextended('{key}', 0))"
    ));
    let (left, right) = std::thread::scope(|scope| {
        let race = |name| send(&competing, part(name, &rows[..1]));
        let left = scope.spawn(move || race("left"));
        let right = scope.spawn(move || race("right"));
        let expired = Instant::now() + Duration::from_secs(10);
        let both_waiting = loop {
            let waiting: i64 = db.query(
                "SELECT count(*) FROM pg_locks held JOIN pg_locks waiting
                USING (locktype,database,classid,objid,objsubid)
                WHERE held.pid=pg_backend_pid() AND held.locktype='advisory'
                AND held.granted AND NOT waiting.granted",
            )[0]
            .get(0);
            if waiting == 2 {
                break true;
            }
            if Instant::now() >= expired {
                break false;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        db.execute("SELECT pg_advisory_unlock_all()");
        assert!(
            both_waiting,
            "both writers must have waited in their own transactions"
        );
        (left.join().unwrap(), right.join().unwrap())
    });
    let mut results = vec![left, right];
    let admitted = results.iter().position(Result::is_ok).unwrap();
    assert_eq!(results.remove(admitted).unwrap().state, State::Open);
    conflict(results.pop().unwrap(), "drafts_open");
    assert_eq!(
        db.query("SELECT count(*) FROM {{schema}}.record_drafts")[0].get::<_, i64>(0),
        16
    );
}
