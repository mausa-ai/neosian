use super::*;
use std::time::{Duration, Instant};

pub(super) fn waits(db: &Database, query: &str, desired: i64) -> bool {
    let end = Instant::now() + Duration::from_secs(6);
    while Instant::now() < end {
        if db.query(query)[0].get::<_, i64>(0) == desired {
            return true;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    false
}

pub(super) const WAITERS:&str=
    "SELECT count(*) FROM pg_locks h JOIN pg_locks w USING(locktype,database,classid,objid,objsubid)
     WHERE h.pid=pg_backend_pid() AND h.locktype='advisory' AND h.granted AND NOT w.granted";

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
enum Operation {
    Migrate,
    Erase,
}

#[derive(Debug, PartialEq, Eq)]
enum ResultOf {
    Migrated(u64, u64, u64),
    Erased(usize),
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn erase_or_another_migration_uses_the_winners_committed_representation() {
    use Operation::{Erase, Migrate};
    use ResultOf::{Erased, Migrated};
    for (first, second, expected) in [
        (Migrate, Erase, (Migrated(1, 0, 0), Erased(1))),
        (Erase, Migrate, (Erased(1), Migrated(0, 0, 1))),
        (Migrate, Migrate, (Migrated(1, 0, 0), Migrated(0, 1, 0))),
    ] {
        let db = Database::new();
        db.store.pool.set_test_repeatable_read().unwrap();
        append(&db.store, ID, "match before migration");
        let original = view(&db.store, 1).unwrap();
        db.execute("SELECT pg_advisory_lock(hashtextextended('{{schema}}:records:migration',0))");
        let apply = |op| match op {
            Migrate => {
                let m = db.store.migrate_records(ID, &LIMITS).unwrap();
                Migrated(m.migrated, m.records, m.skeletons)
            }
            Erase => Erased(
                db.store
                    .redact_turns(ID, None, Some(&[1]), Some("operator"))
                    .unwrap(),
            ),
        };
        let (answers, queued) = std::thread::scope(|scope| {
            let a = scope.spawn(|| apply(first));
            let waited = waits(&db, WAITERS, 1);
            let b = scope.spawn(|| apply(second));
            let both = waits(&db, WAITERS, 2);
            db.execute("SELECT pg_advisory_unlock_all()");
            ((a.join().unwrap(), b.join().unwrap()), waited && both)
        });
        assert!(
            queued,
            "both {first:?} and {second:?} must queue on this conversation"
        );
        assert_eq!(answers, expected);
        let store = db.another_store();
        let acts = store.turn_redactions(None, None, 10).unwrap();
        if first == Erase || second == Erase {
            let turns = store.read_turns(ID, 0, None).unwrap();
            assert_eq!(turns.len(), 1);
            assert!(turns[0].redacted && turns[0].messages.is_empty());
            assert!(rows(&db, "turn_records").is_empty() && rows(&db, "record_turns").is_empty());
            assert_eq!(acts.len(), 1);
            assert_eq!(acts[0].turns, [1]);
        } else {
            assert_eq!(view(&store, 1).unwrap(), original);
            assert_eq!(rows(&db, "turn_records").len(), 1);
            assert_eq!(rows(&db, "record_turns").len(), 1);
            assert!(acts.is_empty());
        }
    }
}
