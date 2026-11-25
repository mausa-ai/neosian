//! Per-turn PostgreSQL migration across real readers, writers and failures.
mod races;
mod suppressed;
mod visibility;

use super::{
    Database,
    restore_stage::{LIMITS, message, rows, stamp},
};
use crate::conversation::turn_records::{Header, RecordRead, Restore, Write, records_of};
use crate::conversation::{Error, Projection};
use crate::postgres::PostgresStore;
use crate::wire::encode;
use serde_json::{Value, json};

const ID: &str = "migration";
// The FileStore migration marker for this conversation.
const MARKER: &str = "m8a6cead4385ed4394247b71692fb729b";

fn append(store: &PostgresStore, id: &str, text: &str) -> u64 {
    store
        .append_turn(
            id,
            vec![message("user", text)],
            Some("source:original".into()),
        )
        .unwrap()
        .turn
}

fn seal(store: &PostgresStore, id: &str) -> crate::conversation::turn_records::Written {
    store
        .write_records(
            id,
            Write {
                draft: MARKER.into(),
                records: records_of(&[message("user", "sealed untouched")]).unwrap(),
                actor: Some("source:seal".into()),
                seal: true,
                ..Write::default()
            },
            &LIMITS,
        )
        .unwrap()
}

fn view(store: &PostgresStore, turn: u64) -> Result<Value, Error> {
    let mut items = Vec::new();
    let page = RecordRead {
        turn: Some(turn),
        ..Default::default()
    };
    let next = store.read_records(ID, &page, &mut |item| {
        items.push(item.to_json());
        Ok(())
    })?;
    let whole = store.read_turns(ID, turn as i64 - 1, Some(1))?;
    let term = store.search_turns(&["needle".into()], Some(&[ID.into()]), 10)?;
    Ok(
        json!({"whole":whole.iter().map(encode::turn).collect::<Vec<_>>(),
        "items":items,"complete":next.is_none(),"search":term.iter().map(encode::turn).collect::<Vec<_>>() }),
    )
}

fn formats(db: &Database) -> Vec<(i32, i32)> {
    db.query("SELECT turn,neosian_format FROM {{schema}}.turns WHERE conversation_id='migration' ORDER BY turn")
        .iter().map(|r|(r.get(0),r.get(1))).collect()
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_late_abort_preserves_the_completed_turn_genuine_seal_and_staged_owner_for_cold_resume() {
    let db = Database::new();
    // The open restore and the genuine seal deliberately use the migration label.
    let records = records_of(&[message("user", "not yet published")]).unwrap();
    db.store
        .restore_records(
            ID,
            Restore {
                restore: MARKER.into(),
                turns: vec![Header::of(8, stamp("2026-10-01T00:00:00Z"), None, &records)],
                records: records.into_iter().map(|r| (8, r)).collect(),
                ..Default::default()
            },
            &LIMITS,
        )
        .unwrap();
    assert_eq!(append(&db.store, ID, "needle first"), 1);
    assert_eq!(append(&db.store, ID, "needle second"), 2);
    let sealed = seal(&db.store, ID);
    assert_eq!(sealed.turn.as_ref().unwrap().turn, 3);
    assert_eq!(seal(&db.store, "other").turn.unwrap().turn, 1);
    db.store
        .append_projections(
            ID,
            &[Projection {
                turn: 2,
                span: 2,
                kind: "log".into(),
                text: "keep".into(),
            }],
        )
        .unwrap();
    let expected = [view(&db.store, 1).unwrap(), view(&db.store, 2).unwrap()];
    let untouched = [
        "record_drafts",
        "record_staging",
        "conversations",
        "projections",
        "turn_redactions",
    ];
    let held = untouched.map(|t| rows(&db, t));
    let all_original = |t: &str| {
        rows(&db, t)
            .into_iter()
            .filter(|r| {
                r["conversation_id"] != "migration"
                    || r["turn"] == 3
                    || r["part"] == 0
                    || r["part"] == 8
            })
            .collect::<Vec<_>>()
    };
    let physical = ["turn_records", "record_turns", "turns"].map(all_original);
    let second = || {
        rows(&db, "turns")
            .into_iter()
            .find(|r| r["turn"] == 2)
            .unwrap()
    };
    let source = second();
    db.execute(
        "CREATE SEQUENCE {{schema}}.witness;
       CREATE FUNCTION {{schema}}.stop_second() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF NEW.conversation_id='migration' AND NEW.turn=2 AND NEW.neosian_format=2 THEN
           IF EXISTS (SELECT FROM {{schema}}.record_turns m JOIN {{schema}}.turn_records r
             USING(conversation_id,owner,part) WHERE m.conversation_id='migration' AND m.turn=2
             AND m.part=2 AND NEW.messages='[]'::jsonb AND NEW.search_text IS NULL) THEN
             PERFORM nextval('{{schema}}.witness');
           END IF;
           RAISE EXCEPTION 'fixture stopped conversion at commit';
         END IF;
         RETURN NULL;
       END $$;
       CREATE CONSTRAINT TRIGGER stop_second AFTER UPDATE ON {{schema}}.turns
       DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {{schema}}.stop_second()",
    );
    assert!(db.store.migrate_records(ID, &LIMITS).is_err());
    assert!(db.query("SELECT is_called FROM {{schema}}.witness")[0].get::<_, bool>(0));
    assert_eq!(formats(&db), [(1, 2), (2, 1), (3, 2)]);
    assert_eq!(second(), source);
    assert!(db.query("SELECT FROM {{schema}}.record_turns WHERE conversation_id='migration' AND turn=2
               UNION ALL SELECT FROM {{schema}}.turn_records WHERE conversation_id='migration' AND part=2").is_empty());
    let first_rows: Vec<_> = db
        .record_addresses()
        .into_iter()
        .filter(|r| r.1 == 1)
        .collect();
    assert_eq!(first_rows.len(), 1);
    db.execute("DROP TRIGGER stop_second ON {{schema}}.turns");
    let cold = db.another_store();
    let result = cold.migrate_records(ID, &LIMITS).unwrap();
    assert_eq!(
        (result.migrated, result.records, result.skeletons),
        (1, 2, 0)
    );
    assert_eq!([view(&cold, 1).unwrap(), view(&cold, 2).unwrap()], expected);
    assert_eq!(untouched.map(|t| rows(&db, t)), held);
    assert_eq!(
        ["turn_records", "record_turns", "turns"].map(all_original),
        physical
    );
    assert_eq!(
        db.record_addresses()
            .into_iter()
            .filter(|r| r.1 == 1)
            .collect::<Vec<_>>(),
        first_rows
    );
    let mappings=db.query("SELECT owner,part,draft FROM {{schema}}.record_turns WHERE conversation_id='migration' ORDER BY turn");
    assert_eq!(
        mappings
            .iter()
            .map(|r| r.get::<_, i32>(1))
            .collect::<Vec<_>>(),
        [1, 2, 0]
    );
    assert_eq!(
        mappings
            .iter()
            .map(|r| r.get::<_, i64>(0))
            .collect::<std::collections::HashSet<_>>()
            .len(),
        3
    );
    assert!(mappings.iter().all(|r| r.get::<_, &str>(2) == MARKER));
    let replay = seal(&cold, ID);
    assert_eq!(replay.turn, sealed.turn);
    assert_eq!((replay.stored_bytes, replay.freed_bytes), (0, 0));
    let remaining = cold
        .restore_records(
            ID,
            Restore {
                restore: MARKER.into(),
                ..Default::default()
            },
            &LIMITS,
        )
        .unwrap();
    assert_eq!(remaining.state.as_str(), "open");
    let second_rows: Vec<_> = db
        .record_addresses()
        .into_iter()
        .filter(|r| r.1 == 2)
        .collect();
    assert_eq!(second_rows.len(), 1);
    assert_eq!(
        cold.redact_turns(ID, None, Some(&[1]), Some("operator"))
            .unwrap(),
        1
    );
    assert_eq!(
        db.record_addresses()
            .into_iter()
            .filter(|r| r.1 == 2)
            .collect::<Vec<_>>(),
        second_rows
    );
    assert_eq!(seal(&cold, ID).turn, sealed.turn);
}
