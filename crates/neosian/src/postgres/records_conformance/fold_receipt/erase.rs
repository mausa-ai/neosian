//! Receipt deletion is part of the redaction transaction, including the
//! zero-visible-turn case. A cold retry can finish after a DB fault is repaired.
use super::*;
use crate::postgres::records_conformance::restore_stage::rows;

const TABLES: [&str; 10] = [
    "conversations",
    "turns",
    "projections",
    "turn_redactions",
    "record_turns",
    "turn_records",
    "record_drafts",
    "record_staging",
    "record_seals",
    "record_seal_pages",
];

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn suppressed_cleanup_rolls_back_updates_and_trail_even_with_no_visible_turn() {
    for table in ["record_seals", "record_seal_pages"] {
        for (full, detached) in [(false, false), (true, false), (true, true)] {
            let db = fixture();
            let data = pieces("hello world", 6);
            let _ = seal(&db.store, ID, "one", &data);
            let sibling = seal(&db.store, ID, "two", &data);
            let outside = seal(&db.store, "other", "one", &data);
            for id in [ID, "other"] {
                assert!(db.store.fold_records(id, false, &LIMITS).unwrap().folded > 0);
            }
            db.execute(&format!("CREATE FUNCTION {{{{schema}}}}.skip_receipt() RETURNS trigger LANGUAGE plpgsql AS $f$
                BEGIN IF OLD.conversation_id='cc-7f3a' THEN RETURN NULL; END IF; RETURN OLD; END; $f$;
                CREATE TRIGGER fault BEFORE DELETE ON {{{{schema}}}}.{table} FOR EACH ROW EXECUTE FUNCTION {{{{schema}}}}.skip_receipt()"));
            if detached {
                db.execute("DELETE FROM {{schema}}.turns WHERE conversation_id='cc-7f3a'");
            }
            let before = TABLES.map(|t| rows(&db, t));
            let selection = if full { None } else { Some(&[1_i64][..]) };
            let err = db
                .store
                .redact_turns(ID, None, selection, None)
                .unwrap_err()
                .text();
            assert!(
                err.contains("could not erase all selected folded seal receipts"),
                "{err}"
            );
            assert_eq!(TABLES.map(|t| rows(&db, t)), before);
            db.execute(&format!("DROP TRIGGER fault ON {{{{schema}}}}.{table}"));
            let cold = db.another_store();
            let count = cold.redact_turns(ID, None, selection, None).unwrap();
            assert_eq!(
                count,
                if detached {
                    0
                } else if full {
                    2
                } else {
                    1
                }
            );
            for relation in ["record_seals", "record_seal_pages"] {
                assert_eq!(
                    db.query(&format!(
                        "SELECT turn FROM {{{{schema}}}}.{relation} WHERE conversation_id='cc-7f3a'"
                    ))
                    .len(),
                    usize::from(!full)
                );
            }
            assert_eq!(rows(&db, "turn_redactions").len(), usize::from(!detached));
            if !full {
                check(&db.another_store(), ID, "two", &data, &sibling);
            }
            check(&db.another_store(), "other", "one", &data, &outside);
        }
    }
}
