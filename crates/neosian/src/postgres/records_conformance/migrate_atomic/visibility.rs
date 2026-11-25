use super::{
    races::{WAITERS, waits},
    *,
};
use std::sync::mpsc;
use std::time::Duration;

fn representation(db: &Database) -> (i32, i64, i64) {
    let row=&db.query(
        "SELECT neosian_format,
          (SELECT count(*) FROM {{schema}}.record_turns WHERE conversation_id='migration' AND turn=1),
          (SELECT count(*) FROM {{schema}}.turn_records WHERE conversation_id='migration' AND part=1)
         FROM {{schema}}.turns WHERE conversation_id='migration' AND turn=1")[0];
    (row.get(0), row.get(1), row.get(2))
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn readers_see_a_whole_turn_across_publication_and_a_later_append_stays_outside_the_run() {
    let db = Database::new();
    let text = format!("needle {}", "a".repeat(150_000));
    append(&db.store, ID, &text);
    let reader = db.another_store();
    let before = view(&reader, 1).unwrap();
    assert_eq!(before["items"].as_array().unwrap().len(), 4);
    assert_eq!(before["complete"], true);
    db.execute(
        "CREATE FUNCTION {{schema}}.wait_for_reader() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN
           IF NEW.neosian_format=2 AND NEW.turn=1 AND NEW.conversation_id='migration' THEN
             PERFORM pg_advisory_xact_lock_shared('{{schema}}'::regnamespace::oid::bigint);
           END IF;
           RETURN NULL;
         END $$;
         CREATE CONSTRAINT TRIGGER wait_for_reader AFTER UPDATE ON {{schema}}.turns
         DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {{schema}}.wait_for_reader();
         SELECT pg_advisory_lock('{{schema}}'::regnamespace::oid::bigint)",
    );
    let limits = crate::limits::Records {
        max_turn_bytes: 65_536,
        ..LIMITS
    };
    let (result, number, paused, append_waited, old, at_wait, after_release, prior_snapshot) =
        std::thread::scope(|scope| {
            let m = scope.spawn(|| db.store.migrate_records(ID, &limits));
            let paused = waits(&db, WAITERS, 1);
            let writer = scope.spawn(|| append(&db.store, ID, "new arrival"));
            // The real append queued behind migration, which itself is blocked
            // in its deferred trigger on the controller's distinct advisory lock.
            let append_waited = waits(
                &db,
                "SELECT count(*) FROM pg_stat_activity parent JOIN pg_stat_activity child
            ON parent.pid=ANY(pg_blocking_pids(child.pid))
            WHERE pg_backend_pid()=ANY(pg_blocking_pids(parent.pid))",
                1,
            );
            let (tx, rx) = mpsc::channel();
            let reader_ref = &reader;
            scope.spawn(move || {
                let _ = tx.send(view(reader_ref, 1));
            });
            let old = rx.recv_timeout(Duration::from_secs(6));
            db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
            let at_wait = representation(&db);
            db.execute("SELECT pg_advisory_unlock_all()");
            let result = m.join().unwrap();
            let number = writer.join().unwrap();
            let after_release = view(&reader, 1);
            let prior_snapshot = representation(&db);
            db.execute("COMMIT");
            (
                result,
                number,
                paused,
                append_waited,
                old,
                at_wait,
                after_release,
                prior_snapshot,
            )
        });
    assert!(
        paused && append_waited,
        "both migration and then the independent append reached their locks"
    );
    assert_eq!(at_wait, (1, 0, 0));
    assert_eq!(prior_snapshot, at_wait);
    assert_eq!(old.unwrap().unwrap(), before);
    assert_eq!(after_release.unwrap(), before);
    let migrated = result.unwrap();
    assert_eq!((migrated.migrated, migrated.records), (1, 0));
    assert_eq!(number, 2);
    assert_eq!(formats(&db), [(1, 2), (2, 1)]);
    assert_eq!(representation(&db), (2, 1, 3));
}
