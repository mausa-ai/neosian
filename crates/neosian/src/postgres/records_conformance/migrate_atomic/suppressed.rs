//! A SQL statement can succeed even though a BEFORE trigger skipped a row.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn skipped_record_owner_header_or_mapping_rolls_back_the_turn_before_retry() {
    for (table, op, condition) in [
        ("record_drafts", "INSERT", "NEW.name=''"),
        ("turn_records", "INSERT", "NEW.seq=1"),
        ("turns", "UPDATE", "NEW.neosian_format=2"),
        ("record_turns", "INSERT", "NEW.turn=1"),
        ("record_drafts", "DELETE", "OLD.name=''"),
    ] {
        let db = Database::new();
        append(&db.store, ID, &"x".repeat(70_000));
        let physical = || {
            [
                "conversations",
                "turns",
                "record_drafts",
                "turn_records",
                "record_turns",
            ]
            .map(|t| rows(&db, t))
        };
        let before = physical();
        db.execute(&format!(
            "CREATE FUNCTION {{{{schema}}}}.skip() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RETURN NULL; END $$;
             CREATE TRIGGER skip BEFORE {op} ON {{{{schema}}}}.{table}
                FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION {{{{schema}}}}.skip()"
        ));
        let result = db.store.migrate_records(ID, &LIMITS);
        assert!(
            result.is_err(),
            "skipped {op} on {table} was accepted: {result:?}"
        );
        assert!(
            physical() == before,
            "skipped {op} on {table} changed stored rows"
        );
        db.execute(&format!("DROP TRIGGER skip ON {{{{schema}}}}.{table}"));
        let retry = db.another_store().migrate_records(ID, &LIMITS).unwrap();
        assert_eq!((retry.migrated, retry.records), (1, 0));
        assert_eq!(formats(&db), [(1, 2)]);
        assert_eq!(db.record_addresses().len(), 2);
        assert_eq!(rows(&db, "record_turns").len(), 1);
        assert!(rows(&db, "record_drafts").is_empty());
    }
}
