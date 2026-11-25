//! Seal, fold, upgrade and re-migration across fresh connections.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn native_replay_survives_cold_fold_remigration_and_shared_names() {
    lifecycle(false);
    lifecycle(true);
}
fn lifecycle(erase_remigrated: bool) {
    let mut db = Database::new();
    if !erase_remigrated {
        db.execute(crate::postgres::sql!("records-postgres-v2"));
    }
    let (first, mut last) = example();
    db.store.restore_records(ID, first, &LIMITS).unwrap();
    last.commit = true;
    let restored = db.store.restore_records(ID, last.clone(), &LIMITS).unwrap();
    let migration = migration_id(ID);
    // One genuine draft collides with the public restore name, another
    // with migration's generated name; neither generated mapping is a seal.
    let data = pieces("hello world", 6);
    let canonical = pieces("hello world", 11);
    let first = seal(&db.store, ID, NAME, &data);
    let second = seal(&db.store, ID, &migration, &data);
    let other = seal(&db.store, "other", NAME, &data);
    assert_eq!((first.turn, second.turn, other.turn), (4, 5, 1));
    if erase_remigrated {
        // This native data was created and replayed by a v1 store. Upgrade it
        // while writers are stopped, then start a store with fresh detection.
        check(&db.another_store(), ID, NAME, &data, &first);
        assert!(db.store.fold_records(ID, false, &LIMITS).is_err());
        db.execute(crate::postgres::sql!("records-postgres-v2"));
        db.store = db.another_store();
    }
    db.store.fold_records("other", false, &LIMITS).unwrap();
    check(&db.another_store(), ID, NAME, &data, &first);
    let f = db.store.fold_records(ID, false, &LIMITS).unwrap();
    assert_eq!((f.folded, f.whole, f.skeletons), (4, 0, 1));
    let digests=db.query("SELECT encode(digests,'hex') FROM {{schema}}.record_seal_pages WHERE conversation_id='cc-7f3a' AND turn=4");
    assert_eq!(
        digests[0].get::<_, String>(0),
        concat!(
            "ef1db5044d9a4759f2c6e1efb0336635a4659f6c2685f10b3aa5c073f1a2245c",
            "553083f93c5d7bffb037200d6fa203e1e2460991f63f3b8e7add7d066daaf560"
        )
    );
    let evidence = snapshot(&db);
    for phase in 0..3 {
        let cold = db.another_store();
        if phase == 1 {
            assert_eq!(cold.migrate_records(ID, &LIMITS).unwrap().migrated, 4);
            let got=db.query("SELECT records,draft,part FROM {{schema}}.record_turns WHERE turn=4 AND conversation_id='cc-7f3a'").remove(0);
            assert_eq!(
                (
                    got.get::<_, i32>(0),
                    got.get::<_, String>(1),
                    got.get::<_, i32>(2)
                ),
                (1, migration.clone(), 4)
            );
        }
        if phase == 2 {
            assert_eq!(cold.fold_records(ID, false, &LIMITS).unwrap().folded, 4);
        }
        // New connection at every transition; original split always wins.
        let cold = db.another_store();
        for (name, head) in [(NAME, &first), (migration.as_str(), &second)] {
            check(&cold, ID, name, &data, head);
            assert_eq!(
                refusal(
                    send(&cold, ID, part(name, &canonical)).unwrap_err(),
                    "draft_mismatch"
                )["seq"],
                0
            );
        }
        let r = cold.restore_records(ID, last.clone(), &LIMITS).unwrap();
        assert_eq!(
            (r.state, r.digest, r.stored_bytes, r.freed_bytes),
            (Staged::Committed, restored.digest.clone(), 0, 0)
        );
        assert_eq!(snapshot(&db), evidence);
        assert_eq!(cold.last_turn_number(ID).unwrap(), 5);
        println!(
            "phase={phase} original_native_ranges=8x2 confirmed; conflict, restore, immutable rows verified"
        );
    }
    if erase_remigrated {
        assert_eq!(
            db.another_store()
                .migrate_records(ID, &LIMITS)
                .unwrap()
                .migrated,
            4
        );
    }
    let cold = db.another_store();
    assert_eq!(cold.redact_turns(ID, None, Some(&[4]), None).unwrap(), 1);
    assert_eq!(
        send(&db.another_store(), ID, part(NAME, &[]))
            .unwrap()
            .state,
        State::None
    );
    check(&db.another_store(), ID, &migration, &data, &second);
    assert_eq!(
        cold.restore_records(ID, last.clone(), &LIMITS)
            .unwrap()
            .digest,
        restored.digest
    );
    check(&db.another_store(), "other", NAME, &data, &other);
    assert_eq!(cold.redact_turns(ID, None, None, None).unwrap(), 5);
    assert_eq!(
        send(&db.another_store(), ID, part(&migration, &[]))
            .unwrap()
            .state,
        State::None
    );
    assert_eq!(
        db.query("SELECT conversation_id FROM {{schema}}.record_seal_pages")
            .iter()
            .map(|r| r.get::<_, String>(0))
            .collect::<Vec<_>>(),
        ["other"]
    );
    check(&db.another_store(), "other", NAME, &data, &other);
    println!(
        "selective and full erase after remigration={erase_remigrated}, retained sibling and cross-conversation identity verified"
    );
}
