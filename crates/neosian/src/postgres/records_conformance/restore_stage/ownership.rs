//! Opening, stale ownership, explicit discard and simultaneous restore writers.
use super::*;
use std::time::{Duration, Instant};

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn an_opening_replaces_only_an_old_restore_after_its_own_validation() {
    let db = Database::new();
    let (first, _) = example();
    send(&db.store, first.clone()).unwrap();
    let mut next = first.clone();
    next.restore = "next".into();
    let before = snapshot(&db);
    assert_conflict(
        send(&db.store, next.clone()),
        "restore_open",
        json!({"restore":NAME}),
    );
    assert_eq!(snapshot(&db), before);
    db.execute(
        "UPDATE {{schema}}.record_drafts SET touched_at=clock_timestamp()-interval '1 hour'",
    );
    let stale = snapshot(&db);
    let probed = send(&db.store, request(NAME)).unwrap();
    assert_eq!(probed.state, Staged::Open);
    assert_eq!(
        snapshot(&db),
        stale,
        "a status probe did not claim the stale owner"
    );
    let mut gap = next.clone();
    gap.turns.clear();
    assert_conflict(
        send(&db.store, gap),
        "restore_gap",
        json!({"position":Position::default().to_json()}),
    );
    assert_eq!(snapshot(&db), stale);
    let refreshed = send(&db.store, first.clone()).unwrap();
    assert_eq!(refreshed, probed);
    let touched = rows(&db, "record_drafts");
    assert_ne!(touched[0]["touched_at"], stale[0][0]["touched_at"]);
    assert_eq!(touched[0]["opened_at"], stale[0][0]["opened_at"]);
    assert_eq!(touched[0]["owner"], stale[0][0]["owner"]);
    assert_conflict(
        send(&db.store, next.clone()),
        "restore_open",
        json!({"restore":NAME}),
    );
    db.execute(
        "UPDATE {{schema}}.record_drafts SET touched_at=clock_timestamp()-interval '1 hour'",
    );
    let replacement = send(&db.store, next).unwrap();
    // The name is shorter by 29 bytes in both the header and the bookkeeping.
    assert_eq!(
        (replacement.stored_bytes, replacement.freed_bytes),
        (3944, 4002)
    );
    let new = &rows(&db, "record_drafts")[0];
    assert_eq!(new["name"], "next");
    assert_ne!(new["owner"], stale[0][0]["owner"]);
    for table in ["record_staging", "turn_records"] {
        assert!(rows(&db, table).iter().all(|r| r["owner"] == new["owner"]));
    }
    assert_eq!(send(&db.store, request(NAME)).unwrap().state, Staged::None);
    // The independently charged byte ledger keeps explicit discard usable
    // even when the validator checkpoint cannot be decoded.
    db.execute("UPDATE {{schema}}.record_drafts SET position='{}'::jsonb");
    let removed = send(
        &db.store,
        Restore {
            discard: true,
            ..request("next")
        },
    )
    .unwrap();
    assert_eq!(
        (removed.state, removed.position, removed.freed_bytes),
        (Staged::Discarded, None, 3944)
    );
    assert!(snapshot(&db).iter().all(Vec::is_empty));
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn targets_need_no_turns_or_projections_but_draft_names_are_independent() {
    let db = Database::new();
    let (first, second) = example();
    db.store
        .append_turn("has-turn", vec![message("user", "existing")], None)
        .unwrap();
    db.store
        .append_projections("has-projection", &second.projections)
        .unwrap();
    for id in ["has-turn", "has-projection"] {
        let got = db.store.restore_records(id, first.clone(), &LIMITS);
        assert!(matches!(got, Err(Error::Conversation(r))
            if r.details == json!({"conversation_id":id,"reason":"target_occupied"})));
    }
    assert!(snapshot(&db).iter().all(Vec::is_empty));
    let draft = Write {
        draft: "shared".into(),
        records: records_of(&[message("user", "private")]).unwrap(),
        ..Write::default()
    };
    db.store.write_records(ID, draft, &LIMITS).unwrap();
    let mut same = first;
    same.restore = "shared".into();
    send(&db.store, same.clone()).unwrap();
    db.store.restore_records("another", same, &LIMITS).unwrap();
    let owners =
        db.query("SELECT count(DISTINCT owner) FROM {{schema}}.record_drafts")[0].get::<_, i64>(0);
    assert_eq!(owners, 3);
    send(
        &db.store,
        Restore {
            discard: true,
            ..request("shared")
        },
    )
    .unwrap();
    assert_eq!(rows(&db, "record_drafts").len(), 2);
    let remaining = db.query(
        "SELECT conversation_id,kind FROM {{schema}}.record_drafts ORDER BY conversation_id",
    );
    assert_eq!(
        (
            remaining[0].get::<_, &str>(0),
            remaining[0].get::<_, &str>(1)
        ),
        ("another", "restore")
    );
    assert_eq!(
        (
            remaining[1].get::<_, &str>(0),
            remaining[1].get::<_, &str>(1)
        ),
        (ID, "write")
    );
    assert_eq!(rows(&db, "turn_records").len(), 4);
}

fn race(db: &Database, names: [&str; 2]) -> [Result<Restored, Error>; 2] {
    let clients = [db.another_store(), db.another_store()];
    for client in &clients {
        client.pool.set_test_repeatable_read().unwrap();
    }
    let key = format!("\"{}\":records:{ID}", db.schema);
    db.execute(&format!(
        "SELECT pg_advisory_lock(hashtextextended('{key}',0))"
    ));
    let (first, _) = example();
    std::thread::scope(|scope| {
        let threads = std::iter::zip(clients, names)
            .map(|(store, name)| {
                let mut part = first.clone();
                part.restore = name.into();
                scope.spawn(move || send(&store, part))
            })
            .collect::<Vec<_>>();
        let until = Instant::now() + Duration::from_secs(10);
        let both_waiting = loop {
            let waiting = db.query(
                "SELECT count(*) FROM pg_locks h JOIN pg_locks w USING
                 (locktype,database,classid,objid,objsubid) WHERE h.pid=pg_backend_pid()
                 AND h.locktype='advisory' AND h.granted AND NOT w.granted",
            )[0]
            .get::<_, i64>(0);
            if waiting == 2 {
                break true;
            }
            if Instant::now() >= until {
                break false;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        db.execute("SELECT pg_advisory_unlock_all()");
        assert!(
            both_waiting,
            "both request transactions reached the database lock"
        );
        let mut joined = threads.into_iter().map(|t| t.join().unwrap());
        [joined.next().unwrap(), joined.next().unwrap()]
    })
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn competing_opens_have_one_owner_and_simultaneous_same_id_retries_charge_once() {
    let db = Database::new();
    let mut separate = race(&db, ["left", "right"]).into_iter().collect::<Vec<_>>();
    let index = separate.iter().position(Result::is_ok).unwrap();
    let good = separate.remove(index).unwrap();
    assert_conflict(
        separate.pop().unwrap(),
        "restore_open",
        json!({"restore":good.restore}),
    );
    assert_eq!(rows(&db, "record_drafts").len(), 1);
    send(
        &db.store,
        Restore {
            discard: true,
            ..request(&good.restore)
        },
    )
    .unwrap();
    let mut charges = race(&db, [NAME, NAME]).map(|result| result.unwrap().stored_bytes);
    charges.sort();
    assert_eq!(charges, [0, 4002]);
    assert_eq!(rows(&db, "record_drafts").len(), 1);
    assert_eq!(rows(&db, "record_staging").len(), 1);
    assert_eq!(rows(&db, "turn_records").len(), 3);
}
