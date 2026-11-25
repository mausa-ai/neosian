use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn oversize_and_later_damaged_records_keep_every_original_tuple_with_discard_requested() {
    let db = Database::new();
    mixed_pending(&db, &"x".repeat(70_000));
    let limits = crate::limits::Records {
        max_turn_bytes: 65_536,
        ..LIMITS
    };
    let expected = snapshot(&db);
    let fail = || db.store.fold_records(ID, true, &limits).unwrap_err().text();
    let oversized = fail();
    assert!(
        oversized.contains("NEOSIAN_MAX_TURN_BYTES")
            && oversized.contains("turn 2")
            && oversized.contains("70000"),
        "{oversized}"
    );
    assert!(snapshot(&db) == expected);
    let digest = db
        .query("SELECT encode(digest,'hex') FROM {{schema}}.turn_records WHERE part=2 AND seq=1")
        [0]
    .get::<_, String>(0);
    db.execute("UPDATE {{schema}}.turn_records SET digest='bad'::bytea WHERE part=2 AND seq=1");
    let corrupt = snapshot(&db);
    assert_eq!(
        fail(),
        oversized,
        "read the size limit before fetching the damaged record"
    );
    let failed = db.store.fold_records(ID, true, &LIMITS);
    assert!(failed.is_err(), "later corrupt record accepted");
    assert!(snapshot(&db) == corrupt);
    db.execute(&format!("UPDATE {{{{schema}}}}.turn_records SET digest=decode('{digest}','hex') WHERE part=2 AND seq=1"));
    for (table, damage, repair) in [
        (
            "turn_records",
            "SET last=true WHERE part=2 AND seq=0",
            "SET last=false WHERE part=2 AND seq=0",
        ),
        (
            "turn_records",
            "SET seq=4 WHERE part=2 AND seq=1",
            "SET seq=1 WHERE part=2 AND seq=4",
        ),
        (
            "record_turns",
            "SET bytes=70001 WHERE turn=2",
            "SET bytes=70000 WHERE turn=2",
        ),
        (
            "record_turns",
            "SET part=7 WHERE turn=2",
            "SET part=2 WHERE turn=2",
        ),
    ] {
        db.execute(&format!("UPDATE {{{{schema}}}}.{table} {damage}"));
        let damaged = snapshot(&db);
        assert!(
            db.store.fold_records(ID, true, &LIMITS).is_err(),
            "accepted {table} {damage}"
        );
        assert!(
            snapshot(&db) == damaged,
            "partial fold after {table} {damage}"
        );
        db.execute(&format!("UPDATE {{{{schema}}}}.{table} {repair}"));
    }
    let cold = db.another_store();
    let folded = cold.fold_records(ID, true, &LIMITS).unwrap();
    assert_eq!((folded.folded, folded.drafts, folded.restores), (2, 1, 1));
    assert_eq!(cold.read_turns(ID, 0, None).unwrap().len(), 2);
    assert_eq!(rows(&db, "record_drafts").len(), 1);
    assert_eq!(rows(&db, "record_drafts")[0]["conversation_id"], "other");
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn deferred_database_abort_restores_native_turns_and_all_pending_owners() {
    let db = Database::new();
    mixed_pending(&db, "visible second");
    let before = snapshot(&db);
    db.execute(
      "CREATE SEQUENCE {{schema}}.witness;
       CREATE FUNCTION {{schema}}.fail_final() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF NEW.conversation_id='cc-7f3a' AND NEW.turn=2 AND NEW.neosian_format=1 THEN
           IF (SELECT count(*) FROM {{schema}}.turns WHERE conversation_id=NEW.conversation_id AND neosian_format=1)=2
             AND NOT EXISTS (SELECT FROM {{schema}}.record_turns WHERE conversation_id=NEW.conversation_id)
             AND NOT EXISTS (SELECT FROM {{schema}}.turn_records WHERE conversation_id=NEW.conversation_id)
             AND NOT EXISTS (SELECT FROM {{schema}}.record_drafts WHERE conversation_id=NEW.conversation_id)
             AND NOT EXISTS (SELECT FROM {{schema}}.record_staging WHERE conversation_id=NEW.conversation_id) THEN
             PERFORM nextval('{{schema}}.witness');
           END IF;
           RAISE EXCEPTION 'fixture interrupted complete fold at commit';
         END IF;
         RETURN NULL;
       END $$;
       CREATE CONSTRAINT TRIGGER fail_final AFTER UPDATE ON {{schema}}.turns
       DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {{schema}}.fail_final()");
    assert!(db.store.fold_records(ID, true, &LIMITS).is_err());
    assert!(db.query("SELECT is_called FROM {{schema}}.witness")[0].get::<_, bool>(0));
    assert!(
        snapshot(&db) == before,
        "earlier turns and pending work must roll back with the final failure"
    );
    db.execute("DROP TRIGGER fail_final ON {{schema}}.turns");
    let cold = db.another_store();
    let result = cold.fold_records(ID, true, &LIMITS).unwrap();
    assert_eq!(
        (result.folded, result.whole, result.drafts, result.restores),
        (2, 0, 1, 1)
    );
    let again = cold.fold_records(ID, true, &LIMITS).unwrap();
    assert_eq!(
        (again.folded, again.whole, again.drafts, again.restores),
        (0, 2, 0, 0)
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn skipped_turn_record_mapping_or_pending_deletions_refuse_the_entire_fold() {
    let db = Database::new();
    mixed_pending(&db, "later source");
    let before = snapshot(&db);
    db.execute("CREATE FUNCTION {{schema}}.skip() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$");
    for (table, op, condition) in [
        ("turns", "UPDATE", "OLD.turn=2"),
        ("record_turns", "DELETE", "OLD.turn=2"),
        ("turn_records", "DELETE", "OLD.part=2"),
        ("record_staging", "DELETE", "OLD.item='turn'"),
        ("turn_records", "DELETE", "OLD.part=0"),
        ("record_drafts", "DELETE", "OLD.kind='restore'"),
        ("record_drafts", "DELETE", "OLD.kind='write'"),
    ] {
        db.execute(&format!("CREATE TRIGGER skip BEFORE {op} ON {{{{schema}}}}.{table} FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION {{{{schema}}}}.skip()"));
        let result = db.store.fold_records(ID, true, &LIMITS);
        db.execute(&format!("DROP TRIGGER skip ON {{{{schema}}}}.{table}"));
        assert!(
            result.is_err(),
            "fold accepted a skipped {op} on {table} ({condition}): {result:?}"
        );
        assert!(
            snapshot(&db) == before,
            "{table} {condition} partially changed stored rows"
        );
    }
    let result = db.another_store().fold_records(ID, true, &LIMITS).unwrap();
    assert_eq!((result.folded, result.drafts, result.restores), (2, 1, 1));
}
