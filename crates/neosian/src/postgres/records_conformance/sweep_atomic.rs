//! The PostgreSQL sweep rechecks open owners under the conversation lock and
//! commits one conversation at a time. Use write-path receipts and SQL tuple
//! identities to check both the charged owners and the untouched siblings.
mod races;

use super::{
    Database,
    restore_stage::{LIMITS, message, rows, stamp},
};
use crate::conversation::turn_records::{Header, Restore, Write, records_of};
use serde_json::{Value, json};

const TABLES: [&str; 8] = [
    "record_drafts",
    "turn_records",
    "record_staging",
    "record_turns",
    "turns",
    "conversations",
    "projections",
    "turn_redactions",
];

fn write(db: &Database, id: &str, name: &str, seal: bool) -> u64 {
    db.store
        .write_records(
            id,
            Write {
                draft: name.into(),
                records: records_of(&[message("user", "some text")]).unwrap(),
                seal,
                ..Write::default()
            },
            &LIMITS,
        )
        .unwrap()
        .stored_bytes
}

fn restore(db: &Database, id: &str, name: &str, commit: bool) -> u64 {
    let records = records_of(&[message("user", "a restored turn")]).unwrap();
    let header = Header::of(4, stamp("2026-10-01T00:00:00Z"), None, &records);
    db.store
        .restore_records(
            id,
            Restore {
                restore: name.into(),
                turns: vec![header],
                records: records.into_iter().map(|r| (4, r)).collect(),
                commit,
                ..Restore::default()
            },
            &LIMITS,
        )
        .unwrap()
        .stored_bytes
}

fn stale(db: &Database, predicate: &str) {
    db.execute(&format!(
        "UPDATE {{{{schema}}}}.record_drafts SET touched_at=now()-interval '45 days' WHERE {predicate}"
    ));
}

fn group(id: &str, drafts: usize, restores: usize, freed: u64) -> Value {
    json!({"conversation_id":id,"drafts":drafts,"restores":restores,"freed_bytes":freed})
}

fn snapshot(db: &Database) -> [Vec<Value>; 8] {
    TABLES.map(|name| rows(db, name))
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn later_abort_keeps_earlier_cleanup_and_rolls_back_every_row_of_the_failed_owner() {
    let db = Database::new();
    restore(&db, "a", "shared", true);
    write(&db, "a", "sealed", true);
    write(&db, "a", "fresh", false);
    write(&db, "a", "shared", false);
    let a_owner: i64 = db.query(
        "SELECT owner FROM {{schema}}.record_drafts
                                      WHERE conversation_id='a' AND kind='write' AND name='shared'",
    )[0]
    .get(0);
    let b_charge = write(&db, "b", "shared", false) + restore(&db, "b", "shared", false);
    write(&db, "b", "fresh", false);
    let c_charge = write(&db, "c", "shared", false);
    stale(&db, "name='shared' AND state='open'");
    let mut after_a = snapshot(&db);
    for table in &mut after_a {
        table.retain(|r| !(r["conversation_id"] == "a" && r["owner"] == a_owner));
    }
    db.execute(
        "CREATE SEQUENCE {{schema}}.reached;
         CREATE FUNCTION {{schema}}.fail_b() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN
           IF OLD.conversation_id='b' AND OLD.kind='restore' THEN
             IF NOT EXISTS (SELECT FROM {{schema}}.record_drafts WHERE conversation_id='b' AND name='shared')
                AND NOT EXISTS (SELECT FROM {{schema}}.turn_records WHERE conversation_id='b' AND owner=OLD.owner)
                AND NOT EXISTS (SELECT FROM {{schema}}.record_staging WHERE conversation_id='b' AND owner=OLD.owner) THEN
               PERFORM nextval('{{schema}}.reached');
             END IF;
             RAISE EXCEPTION 'fixture interrupted b at commit';
           END IF;
           RETURN NULL;
         END $$;
         CREATE CONSTRAINT TRIGGER stop_b AFTER DELETE ON {{schema}}.record_drafts
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {{schema}}.fail_b()");
    assert!(db.store.sweep_records(3, None, true).is_err());
    assert!(db.query("SELECT is_called FROM {{schema}}.reached")[0].get::<_, bool>(0));
    assert_eq!(
        snapshot(&db),
        after_a,
        "a committed, b rolled back and c was never visited"
    );
    db.execute("DROP TRIGGER stop_b ON {{schema}}.record_drafts");
    let cold = db.another_store();
    let retry = cold.sweep_records(3, None, true).unwrap();
    assert_eq!(
        serde_json::to_value(retry.conversations).unwrap(),
        json!([group("b", 1, 1, b_charge), group("c", 1, 0, c_charge)])
    );
    assert!(retry.next_cursor.is_none());
    let removed: Vec<_> = after_a[0]
        .iter()
        .filter(|r| r["state"] == "open" && r["name"] == "shared")
        .map(|r| r["owner"].as_i64().unwrap())
        .collect();
    assert_eq!(removed.len(), 3);
    for table in &mut after_a {
        table.retain(|r| r["owner"].as_i64().is_none_or(|n| !removed.contains(&n)));
    }
    assert_eq!(snapshot(&db), after_a);
    assert!(
        cold.sweep_records(3, None, true)
            .unwrap()
            .conversations
            .is_empty()
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn unreadable_positions_keep_their_typed_charges_and_do_not_rewrite_fresh_siblings() {
    let db = Database::new();
    restore(&db, "proj", "shared", false);
    write(&db, "proj", "shared", false);
    write(&db, "proj", "fresh", false);
    stale(&db, "name='shared'");
    db.execute(
        "UPDATE {{schema}}.record_drafts SET position='null'::jsonb,
                bytes=CASE WHEN kind='restore' THEN 2222222 ELSE 1111111 END WHERE name='shared'",
    );
    let mut survivors = snapshot(&db);
    assert_eq!(survivors[0].len(), 3);
    let doomed: Vec<_> = survivors[0]
        .iter()
        .filter(|r| r["name"] == "shared")
        .map(|r| r["owner"].as_i64().unwrap())
        .collect();
    let expected = json!([group("proj", 1, 1, 3_333_333)]);
    let page = db.store.sweep_records(1, None, false).unwrap();
    assert_eq!(serde_json::to_value(page.conversations).unwrap(), expected);
    assert!(page.next_cursor.is_none());
    assert_eq!(snapshot(&db), survivors);
    let page = db.store.sweep_records(1, None, true).unwrap();
    assert_eq!(serde_json::to_value(page.conversations).unwrap(), expected);
    for table in &mut survivors {
        table.retain(|r| r["owner"].as_i64().is_none_or(|n| !doomed.contains(&n)));
    }
    assert_eq!(snapshot(&db), survivors);
}
