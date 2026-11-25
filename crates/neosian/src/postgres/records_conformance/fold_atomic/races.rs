use super::*;
use std::time::{Duration, Instant};

pub(super) fn waiting(db: &Database, desired: i64) -> bool {
    let end = Instant::now() + Duration::from_secs(6);
    while Instant::now() < end {
        let actual=db.query(
            "SELECT count(*) FROM pg_locks h JOIN pg_locks w USING(locktype,database,classid,objid,objsubid)
             WHERE h.pid=pg_backend_pid() AND h.locktype='advisory' AND h.granted AND NOT w.granted"
        )[0].get::<_,i64>(0);
        if actual == desired {
            return true;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    false
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Op {
    Fold,
    Erase,
    Migrate,
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn fold_and_an_erase_or_migrate_consume_the_previous_winners_committed_turn() {
    use Op::*;
    for (a, b, expected, format) in [
        (Fold, Erase, ([1, 0, 0], [1, 0, 0]), 1),
        (Erase, Fold, ([1, 0, 0], [0, 0, 1]), 1),
        (Fold, Migrate, ([0, 1, 0], [1, 0, 0]), 2),
        (Migrate, Fold, ([1, 0, 0], [1, 0, 0]), 1),
    ] {
        let db = Database::new();
        db.store.pool.set_test_repeatable_read().unwrap();
        append(&db.store, ID, "racing turn");
        let erase = a == Erase || b == Erase;
        if erase {
            assert_eq!(db.store.migrate_records(ID, &LIMITS).unwrap().migrated, 1);
        }
        let before = answers(&db.store).unwrap();
        db.execute("SELECT pg_advisory_lock(hashtextextended('{{schema}}:records:cc-7f3a',0))");
        let request = |op| match op {
            Fold => {
                let f = db.store.fold_records(ID, false, &LIMITS).unwrap();
                [f.folded, f.whole, f.skeletons]
            }
            Erase => [
                db.store
                    .redact_turns(ID, None, Some(&[1]), Some("operator"))
                    .unwrap() as u64,
                0,
                0,
            ],
            Migrate => {
                let m = db.store.migrate_records(ID, &LIMITS).unwrap();
                [m.migrated, m.records, m.skeletons]
            }
        };
        let actual = std::thread::scope(|scope| {
            let first = scope.spawn(|| request(a));
            let one = waiting(&db, 1);
            let second = scope.spawn(|| request(b));
            let two = waiting(&db, 2);
            db.execute("SELECT pg_advisory_unlock_all()");
            let values = (first.join().unwrap(), second.join().unwrap());
            assert!(one && two, "{a:?}, then {b:?} must queue on the same lock");
            values
        });
        assert_eq!(actual, expected);
        assert_eq!(rows(&db, "turns")[0]["neosian_format"], format);
        assert_eq!(rows(&db, "record_turns").len(), usize::from(format == 2));
        assert_eq!(rows(&db, "turn_records").len(), usize::from(format == 2));
        let cold = db.another_store();
        if erase {
            let turns = cold.read_turns(ID, 0, None).unwrap();
            assert!(turns[0].redacted && turns[0].messages.is_empty());
            let trail = cold.turn_redactions(None, None, 10).unwrap();
            assert_eq!(trail.len(), 1);
            assert_eq!(trail[0].turns, [1]);
        } else {
            assert_eq!(answers(&cold).unwrap(), before);
        }
    }
}
