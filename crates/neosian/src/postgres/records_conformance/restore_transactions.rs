//! A database error after staging a replacement must roll back its rows and
//! leave the stale restore recoverable, including its original byte charge.
use super::Database;
use crate::conversation::message;
use crate::conversation::turn_records::{Header, Restore, Staged, records_of};
use crate::limits::Records;
use crate::store::time::Stamp;
use serde_json::json;

const ID: &str = "recovery";
const LIMITS: Records = Records {
    draft_seconds: 60,
    max_turn_bytes: 64 << 20,
    max_record_turn_bytes: 1 << 30,
};

fn probe(name: &str) -> Restore {
    Restore {
        restore: name.into(),
        ..Restore::default()
    }
}

fn part(name: &str, turn: u64, text: &str) -> Restore {
    let message = message::from_json(&json!({"role":"user","content":text})).unwrap();
    let records = records_of(&[message]).unwrap();
    let stamp = Stamp::parse("2026-10-25T10:00:00.123456Z").unwrap();
    Restore {
        turns: vec![Header::of(turn, stamp, None, &records)],
        records: records.into_iter().map(|r| (turn, r)).collect(),
        ..probe(name)
    }
}

fn stored_rows(db: &Database) -> Vec<String> {
    db.query(
        "SELECT to_jsonb(d)::text AS row FROM {{schema}}.record_drafts d
         UNION ALL SELECT to_jsonb(s)::text FROM {{schema}}.record_staging s
         UNION ALL SELECT to_jsonb(r)::text FROM {{schema}}.turn_records r ORDER BY row",
    )
    .into_iter()
    .map(|row| row.get(0))
    .collect()
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn aborted_replacement_preserves_the_previous_stream_and_all_its_bytes() {
    let db = Database::new();
    let old = db
        .store
        .restore_records(ID, part("old", 7, "original text"), &LIMITS)
        .unwrap();
    db.execute("UPDATE {{schema}}.record_drafts SET touched_at = now() - interval '2 hours'");
    let before = stored_rows(&db);
    assert_eq!(before.len(), 3);
    // The sequence witnesses the deferred trigger even after its transaction
    // rolls back. It runs once, only after the new items and state are in place.
    db.execute(
        "CREATE SEQUENCE {{schema}}.abort_count;
         CREATE FUNCTION {{schema}}.fail_replacement() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN
           IF NOT EXISTS (SELECT FROM {{schema}}.record_drafts WHERE name = 'old')
              AND EXISTS (SELECT FROM {{schema}}.record_drafts WHERE name = 'new'
                          AND state = 'open' AND owner = NEW.owner AND bytes > 0)
              AND EXISTS (SELECT FROM {{schema}}.record_staging WHERE owner = NEW.owner)
           THEN
             PERFORM nextval('{{schema}}.abort_count');
             RAISE EXCEPTION 'stop the replacement at commit';
           END IF;
           RETURN NULL;
         END $$;
         CREATE CONSTRAINT TRIGGER abort_replacement AFTER INSERT ON {{schema}}.turn_records
         DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
         EXECUTE FUNCTION {{schema}}.fail_replacement()",
    );

    let replacement = part("new", 9, "replacement text");
    assert!(
        db.store
            .restore_records(ID, replacement.clone(), &LIMITS)
            .is_err()
    );
    let witnessed = db.query("SELECT last_value, is_called FROM {{schema}}.abort_count");
    assert!(witnessed[0].get::<_, bool>(1));
    assert_eq!(witnessed[0].get::<_, i64>(0), 1);
    assert_eq!(stored_rows(&db), before);

    let reopened = db.another_store();
    let resumed = reopened.restore_records(ID, probe("old"), &LIMITS).unwrap();
    assert_eq!(resumed.state, Staged::Open);
    assert_eq!(resumed.position, old.position);
    assert_eq!((resumed.stored_bytes, resumed.freed_bytes), (0, 0));
    let missing = reopened.restore_records(ID, probe("new"), &LIMITS).unwrap();
    assert_eq!(missing.state, Staged::None);
    assert_eq!(reopened.last_turn_number(ID).unwrap(), 0);
    assert!(reopened.conversations().unwrap().is_empty());

    db.execute("DROP TRIGGER abort_replacement ON {{schema}}.turn_records");
    let retried = reopened.restore_records(ID, replacement, &LIMITS).unwrap();
    assert_eq!(retried.state, Staged::Open);
    assert_eq!(retried.freed_bytes, old.stored_bytes);
    let discard = Restore {
        discard: true,
        ..probe("new")
    };
    let discarded = reopened.restore_records(ID, discard, &LIMITS).unwrap();
    assert_eq!(discarded.freed_bytes, retried.stored_bytes);
    assert!(stored_rows(&db).is_empty());
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_database_abort_after_publication_restores_the_old_position_and_allows_a_retry() {
    let db = Database::new();
    let opened = db
        .store
        .restore_records(ID, part("trip", 7, "first"), &LIMITS)
        .unwrap();
    let before = stored_rows(&db);
    db.execute(
        "CREATE SEQUENCE {{schema}}.publish_count;
         CREATE FUNCTION {{schema}}.fail_publish() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN
           IF (SELECT count(*) FROM {{schema}}.turns) = 2
              AND (SELECT count(*) FROM {{schema}}.record_turns) = 2
              AND NOT EXISTS (SELECT FROM {{schema}}.record_staging)
              AND EXISTS (SELECT FROM {{schema}}.record_drafts WHERE state = 'committed')
           THEN
             PERFORM nextval('{{schema}}.publish_count');
             RAISE EXCEPTION 'stop publication at transaction commit';
           END IF;
           RETURN NULL;
         END $$;
         CREATE CONSTRAINT TRIGGER abort_publish AFTER INSERT ON {{schema}}.conversations
         DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {{schema}}.fail_publish()",
    );
    let finish = Restore {
        commit: true,
        ..part("trip", 9, "final")
    };
    assert!(
        db.store
            .restore_records(ID, finish.clone(), &LIMITS)
            .is_err()
    );
    let witnessed = db.query("SELECT last_value, is_called FROM {{schema}}.publish_count");
    assert_eq!(witnessed[0].get::<_, i64>(0), 1);
    assert!(witnessed[0].get::<_, bool>(1));
    assert_eq!(stored_rows(&db), before);
    let reopened = db.another_store();
    let probe = reopened
        .restore_records(ID, probe("trip"), &LIMITS)
        .unwrap();
    assert_eq!(probe.position, opened.position);
    assert_eq!(probe.state, Staged::Open);
    assert_eq!(reopened.last_turn_number(ID).unwrap(), 0);
    assert!(reopened.conversations().unwrap().is_empty());
    let visible = db.query(
        "SELECT (SELECT count(*) FROM {{schema}}.record_turns)
              + (SELECT count(*) FROM {{schema}}.turns)
              + (SELECT count(*) FROM {{schema}}.conversations)",
    );
    assert_eq!(visible[0].get::<_, i64>(0), 0);

    db.execute("DROP TRIGGER abort_publish ON {{schema}}.conversations");
    let committed = reopened
        .restore_records(ID, finish.clone(), &LIMITS)
        .unwrap();
    assert_eq!(committed.state, Staged::Committed);
    let read = reopened.read_turns(ID, 0, None).unwrap();
    assert_eq!(read.iter().map(|t| t.turn).collect::<Vec<_>>(), [7, 9]);
    let replay = db
        .another_store()
        .restore_records(ID, finish, &LIMITS)
        .unwrap();
    assert_eq!(replay.state, Staged::Committed);
    assert_eq!(replay.digest, committed.digest);
    assert_eq!(replay.position, committed.position);
    assert_eq!((replay.stored_bytes, replay.freed_bytes), (0, 0));
}
