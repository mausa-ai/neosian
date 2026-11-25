use super::*;
use std::sync::mpsc;
use std::time::Duration;

fn counts(db: &Database) -> [i64; 4] {
    let row=&db.query(
      "SELECT (SELECT count(*) FROM {{schema}}.turns WHERE conversation_id='cc-7f3a' AND neosian_format=2),
              (SELECT count(*) FROM {{schema}}.record_turns WHERE conversation_id='cc-7f3a'),
              (SELECT count(*) FROM {{schema}}.turn_records WHERE conversation_id='cc-7f3a'),
              (SELECT count(*) FROM {{schema}}.record_drafts WHERE conversation_id='cc-7f3a' AND state='open')")[0];
    [row.get(0), row.get(1), row.get(2), row.get(3)]
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn both_restore_parts_discard_and_every_public_reader_cross_a_single_commit() {
    let db = Database::new();
    let (closing, receipt) = published(&db);
    pending(&db, "other");
    let original = snapshot(&db);
    let unrelated = || {
        snapshot(&db).map(|table| {
            table
                .into_iter()
                .filter(|r| r["conversation_id"] == "other")
                .collect::<Vec<_>>()
        })
    };
    let other = unrelated();
    let preserved = || {
        [
            "record_drafts",
            "turns",
            "projections",
            "turn_redactions",
            "conversations",
        ]
        .map(|name| {
            rows(&db, name)
                .into_iter()
                .filter(|r| {
                    r["conversation_id"] == ID
                        && match name {
                            "record_drafts" => r["state"] == "committed",
                            "turns" => r["turn"] == 2 || r["turn"] == 4,
                            _ => true,
                        }
                })
                .collect::<Vec<_>>()
        })
    };
    let originals = preserved();
    let mappings = rows(&db, "record_turns");
    assert_eq!(mappings.len(), 2);
    assert_eq!(mappings[0]["owner"], mappings[1]["owner"]);
    let before = counts(&db);
    assert_eq!(before, [2, 2, 5, 1]);
    let whole = db.another_store();
    let expected = answers(&whole).unwrap();
    assert_eq!(expected["complete"], true);
    let search = expected["search"].as_array().unwrap();
    for (index, n) in [(0, 1), (1, 1), (2, 3), (3, 3), (4, 4)] {
        assert_eq!(search[index][0][0]["turn"], n);
    }
    assert!(search[6].as_array().unwrap().is_empty());
    assert!(
        db.store
            .fold_records(ID, false, &LIMITS)
            .unwrap_err()
            .text()
            .contains("pending work")
    );
    assert!(snapshot(&db) == original);
    db.execute(
        "CREATE FUNCTION {{schema}}.pause_fold() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN
           IF NEW.conversation_id='cc-7f3a' AND NEW.turn=3 AND NEW.neosian_format=1 THEN
             PERFORM pg_advisory_xact_lock_shared('{{schema}}'::regnamespace::oid::bigint);
           END IF;
           RETURN NULL;
         END $$;
         CREATE CONSTRAINT TRIGGER pause_fold AFTER UPDATE ON {{schema}}.turns
         DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {{schema}}.pause_fold();
         SELECT pg_advisory_lock('{{schema}}'::regnamespace::oid::bigint)",
    );
    let (result, paused, live, at_wait, old_snapshot) = std::thread::scope(|scope| {
        let op = scope.spawn(|| db.store.fold_records(ID, true, &LIMITS));
        let paused = races::waiting(&db, 1);
        let (tx, rx) = mpsc::channel();
        let store = &whole;
        scope.spawn(move || {
            let _ = tx.send(answers(store));
        });
        let live = rx.recv_timeout(Duration::from_secs(6));
        db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
        let at_wait = counts(&db);
        db.execute("SELECT pg_advisory_unlock_all()");
        let result = op.join().unwrap();
        let old_snapshot = counts(&db);
        db.execute("COMMIT");
        (result, paused, live, at_wait, old_snapshot)
    });
    assert!(paused);
    assert_eq!(at_wait, before);
    assert_eq!(old_snapshot, before);
    assert_eq!(live.unwrap().unwrap(), expected);
    let folded = result.unwrap();
    assert_eq!(
        (
            folded.folded,
            folded.whole,
            folded.skeletons,
            folded.drafts,
            folded.restores
        ),
        (2, 1, 1, 1, 0)
    );
    assert_eq!(counts(&db), [0, 0, 0, 0]);
    let cold = db.another_store();
    assert_eq!(answers(&cold).unwrap(), expected);
    assert_eq!(preserved(), originals);
    assert_eq!(unrelated(), other);
    let again = cold.fold_records(ID, false, &LIMITS).unwrap();
    assert_eq!((again.folded, again.whole, again.skeletons), (0, 3, 1));
    let replay = cold.restore_records(ID, closing, &LIMITS).unwrap();
    assert_eq!(replay.state.as_str(), "committed");
    assert_eq!((replay.stored_bytes, replay.freed_bytes), (0, 0));
    assert_eq!(replay.to_json()["digest"], receipt["digest"]);
    assert_eq!(replay.to_json()["position"], receipt["position"]);
}
