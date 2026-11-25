//! Actual physical rows, deferred transaction aborts and selectors in a shared
//! restore owner. Every snapshot includes PostgreSQL tuple identity.
mod races;

use super::{
    Database,
    restore_stage::{LIMITS, message, rows, stamp},
};
use crate::conversation::turn_records::{Header, Restore, Write, records_of};
use crate::conversation::{Error, Projection};
use crate::postgres::PostgresStore;
use serde_json::Value;

const TABLES: [&str; 8] = [
    "conversations",
    "turns",
    "projections",
    "turn_redactions",
    "record_turns",
    "turn_records",
    "record_staging",
    "record_drafts",
];
const ID: &str = "target";
const OTHER: &str = "other";
const NAME: &str = "shared";

fn restore_part(number: u64, text: &str, commit: bool) -> Restore {
    let records = records_of(&[message("user", text), message("assistant", "recorded")]).unwrap();
    let header = Header::of(
        number,
        stamp("2026-10-05T04:00:03Z"),
        Some("archive".into()),
        &records,
    );
    Restore {
        restore: NAME.into(),
        turns: vec![header],
        records: records.into_iter().map(|r| (number, r)).collect(),
        commit,
        ..Restore::default()
    }
}

fn write(store: &PostgresStore, id: &str, name: &str, text: &str, seal: bool) {
    store
        .write_records(
            id,
            Write {
                draft: name.into(),
                records: records_of(&[message("user", text)]).unwrap(),
                seal,
                ..Write::default()
            },
            &LIMITS,
        )
        .unwrap();
}

fn projection(turn: u64, text: &str) -> Projection {
    Projection {
        turn,
        span: 1,
        kind: "log".into(),
        text: text.into(),
    }
}

fn install(db: &Database, id: &str) {
    let first = restore_part(4, "erased", false);
    db.store.restore_records(id, first, &LIMITS).unwrap();
    let mut last = restore_part(11, "retained", true);
    last.projections_from = Some(0);
    last.projections = vec![projection(4, "first"), projection(11, "sibling")];
    db.store.restore_records(id, last, &LIMITS).unwrap();
    // A sealed write can have the same public name as the restore, but has
    // a different owner, and it always uses physical part zero.
    write(&db.store, id, NAME, "third", true);
    write(&db.store, id, "pending", "draft", false);
}

fn snapshot(db: &Database) -> [Vec<Value>; 8] {
    TABLES.map(|table| rows(db, table))
}

fn belonging(db: &Database, table: &str, id: &str) -> Vec<Value> {
    rows(db, table)
        .into_iter()
        .filter(|r| r["conversation_id"] == id)
        .collect()
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn deleting_one_restore_part_keeps_every_other_physical_tuple() {
    let db = Database::new();
    install(&db, ID);
    install(&db, OTHER);
    let other = TABLES.map(|t| belonging(&db, t, OTHER));
    let mut physical = rows(&db, "turn_records");
    let mut mappings = rows(&db, "record_turns");
    let target = belonging(&db, "record_turns", ID);
    let key = |turn| target.iter().find(|r| r["turn"] == turn).unwrap();
    assert_eq!(key(4)["owner"], key(11)["owner"]);
    assert_ne!(key(4)["owner"], key(12)["owner"]);
    assert_eq!(key(4)["draft"], key(12)["draft"]);
    assert_eq!(key(12)["part"], 0);
    physical.retain(|r| r["conversation_id"] != ID || r["part"] != 4);
    mappings.retain(|r| r["conversation_id"] != ID || r["turn"] != 4);
    let sibling: Vec<_> = physical
        .iter()
        .filter(|row| row["conversation_id"] == ID && row["part"] == 11)
        .map(|row| row["seq"].as_i64().unwrap())
        .collect();
    assert_eq!(sibling.len(), 2);
    assert!(sibling.contains(&0) && sibling.contains(&1));
    let work = rows(&db, "record_drafts");
    assert_eq!(
        db.store
            .redact_turns(ID, None, Some(&[4, 4]), Some("one"))
            .unwrap(),
        1
    );
    assert_eq!(rows(&db, "turn_records"), physical);
    assert_eq!(rows(&db, "record_turns"), mappings);
    assert_eq!(rows(&db, "record_drafts"), work);
    assert_eq!(TABLES.map(|t| belonging(&db, t, OTHER)), other);
    let projected = db.store.read_projections(ID, 0, None).unwrap();
    assert_eq!(projected, [projection(4, ""), projection(11, "sibling")]);
    let read = db.another_store().read_turns(ID, 0, None).unwrap();
    assert!(read[0].redacted && read[0].messages.is_empty());
    assert_eq!(read.iter().map(|t| t.turn).collect::<Vec<_>>(), [4, 11, 12]);
    assert_eq!(
        db.store.redact_turns(ID, None, None, Some("all")).unwrap(),
        3
    );
    assert_eq!(TABLES.map(|t| belonging(&db, t, OTHER)), other);
    for t in [
        "record_drafts",
        "turn_records",
        "record_staging",
        "record_turns",
    ] {
        assert!(belonging(&db, t, ID).is_empty(), "{t}");
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn aborting_full_erasure_rolls_back_hidden_work_visible_rows_and_trail() {
    let db = Database::new();
    db.store
        .restore_records(ID, restore_part(9, "staged", false), &LIMITS)
        .unwrap();
    write(&db.store, ID, "sealed", "first", true);
    write(&db.store, ID, "pending", "private draft", false);
    db.store
        .append_turn(ID, vec![message("user", "whole")], None)
        .unwrap();
    db.store
        .append_projections(ID, &[projection(2, "remove")])
        .unwrap();
    let original = snapshot(&db);
    db.execute(
        "CREATE SEQUENCE {{schema}}.reached;
         CREATE FUNCTION {{schema}}.fail_last() RETURNS TRIGGER LANGUAGE plpgsql AS $$
         BEGIN
           IF NOT EXISTS (SELECT FROM {{schema}}.record_turns)
              AND NOT EXISTS (SELECT FROM {{schema}}.turn_records)
              AND NOT EXISTS (SELECT FROM {{schema}}.record_drafts)
              AND NOT EXISTS (SELECT FROM {{schema}}.record_staging)
              AND (SELECT count(*) FROM {{schema}}.turns WHERE redacted AND neosian_format=1) = 2
              AND (SELECT count(*) FROM {{schema}}.projections WHERE text='') = 1
              AND NEW.turns = ARRAY[1,2] THEN
               PERFORM nextval('{{schema}}.reached');
           END IF;
           RAISE EXCEPTION 'fixture interrupted full erase at commit';
         END $$;
         CREATE CONSTRAINT TRIGGER stop_final AFTER INSERT ON {{schema}}.turn_redactions
         DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {{schema}}.fail_last()",
    );
    assert!(
        db.store
            .redact_turns(ID, None, None, Some("operator"))
            .is_err()
    );
    let signal = db.query("SELECT is_called,last_value FROM {{schema}}.reached");
    assert!(
        signal[0].get::<_, bool>(0),
        "deferred trigger must witness all mutations"
    );
    assert_eq!(signal[0].get::<_, i64>(1), 1);
    assert_eq!(snapshot(&db), original);
    db.execute("DROP TRIGGER stop_final ON {{schema}}.turn_redactions");
    assert_eq!(
        db.another_store()
            .redact_turns(ID, None, None, Some("operator"))
            .unwrap(),
        2
    );
    let trail = db.store.turn_redactions(None, None, 10).unwrap();
    assert_eq!(trail.len(), 1);
    assert_eq!(trail[0].turns, [1, 2]);
    assert_eq!(trail[0].actor.as_deref(), Some("operator"));
    assert!(
        db.store
            .read_turns(ID, 0, None)
            .unwrap()
            .iter()
            .all(|t| t.redacted && t.messages.is_empty())
    );
    for table in [
        "record_drafts",
        "record_staging",
        "turn_records",
        "record_turns",
    ] {
        assert!(rows(&db, table).is_empty(), "{table}");
    }
}

fn future(e: Error) {
    let Error::Conversation(refusal) = e else {
        panic!("expected an unsupported-format refusal, got {e:?}")
    };
    assert_eq!(refusal.code, "agent_conversation_format_unsupported");
    assert!(refusal.message.contains("77"), "{}", refusal.message);
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn future_formats_refuse_only_when_affected_with_no_partial_changes() {
    let db = Database::new();
    install(&db, ID);
    for (table, clause, restore_format) in [
        ("turns", "turn=4", 2),
        ("projections", "turn=4", 1),
        ("turn_records", "part=4", 1),
    ] {
        let update = |format| {
            db.execute(&format!(
                "UPDATE {{{{schema}}}}.{table} SET neosian_format={format} WHERE {clause}"
            ))
        };
        update(77);
        let before = snapshot(&db);
        future(
            db.store
                .redact_turns(ID, None, Some(&[4]), None)
                .unwrap_err(),
        );
        assert_eq!(snapshot(&db), before);
        update(restore_format);
    }
    // An unrecognized record in an open draft is outside a selective write
    // but is affected by a whole-conversation erasure.
    db.execute(
        "UPDATE {{schema}}.turn_records r SET neosian_format=77 FROM {{schema}}.record_drafts d
                WHERE d.name='pending' AND (r.conversation_id,r.owner)=(d.conversation_id,d.owner)",
    );
    assert_eq!(
        db.store.redact_turns(ID, None, Some(&[4]), None).unwrap(),
        1
    );
    let before = snapshot(&db);
    future(db.store.redact_turns(ID, None, None, None).unwrap_err());
    assert_eq!(snapshot(&db), before);
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn both_header_forms_refuse_inconsistent_selected_mapping_metadata() {
    let db = Database::new();
    install(&db, ID);
    for (damage, repair) in [
        (
            "UPDATE {{schema}}.turns SET neosian_format=1 WHERE turn=4",
            "UPDATE {{schema}}.turns SET neosian_format=2 WHERE turn=4",
        ),
        (
            "UPDATE {{schema}}.record_turns SET records=records+1 WHERE turn=4",
            "UPDATE {{schema}}.record_turns SET records=records-1 WHERE turn=4",
        ),
        (
            "UPDATE {{schema}}.record_turns SET bytes=bytes+1 WHERE turn=4",
            "UPDATE {{schema}}.record_turns SET bytes=bytes-1 WHERE turn=4",
        ),
        (
            "UPDATE {{schema}}.record_turns SET created_at=created_at+interval '1 second' WHERE turn=4",
            "UPDATE {{schema}}.record_turns SET created_at=created_at-interval '1 second' WHERE turn=4",
        ),
        (
            "UPDATE {{schema}}.turn_records SET seq=3 WHERE part=4 AND seq=1",
            "UPDATE {{schema}}.turn_records SET seq=1 WHERE part=4 AND seq=3",
        ),
    ] {
        db.execute(damage);
        let before = snapshot(&db);
        let Err(Error::Conversation(no)) = db.store.redact_turns(ID, None, Some(&[4]), None) else {
            panic!("expected an unsupported-format refusal for {damage}");
        };
        assert_eq!(no.code, "agent_conversation_format_unsupported");
        assert_eq!(
            snapshot(&db),
            before,
            "refused erase changed rows for {damage}"
        );
        db.execute(repair);
    }
    db.execute("UPDATE {{schema}}.turns SET neosian_format=1 WHERE turn=4");
    let before: Vec<_> = belonging(&db, "turn_records", ID)
        .into_iter()
        .filter(|r| r["part"] == 4)
        .collect();
    assert_eq!(
        db.store.redact_turns(ID, None, Some(&[11]), None).unwrap(),
        1
    );
    let after: Vec<_> = belonging(&db, "turn_records", ID)
        .into_iter()
        .filter(|r| r["part"] == 4)
        .collect();
    assert_eq!(after, before);
}
