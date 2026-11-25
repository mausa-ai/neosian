//! Failed publication of new or previously staged items leaves the old stream intact.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn incoming_and_already_staged_dangling_references_refuse_at_the_pre_request_position() {
    for previously_staged in [false, true] {
        for projection in [false, true] {
            let db = Database::new();
            let (first, mut bad) = example();
            let mut at = send(&db.store, first).unwrap().position.unwrap();
            if projection {
                bad.projections[0].turn = 8;
            } else {
                bad.redactions[0].turns.push(8);
            }
            if previously_staged {
                at = send(&db.store, bad).unwrap().position.unwrap();
                bad = request(NAME);
            }
            let before = snapshot(&db);
            assert_conflict(
                send(&db.store, commit(bad)),
                "restore_incomplete",
                json!({"position":at.to_json()}),
            );
            assert_eq!(snapshot(&db), before);
            assert_invisible(&db);
            let reopened = db.another_store();
            assert_eq!(send(&reopened, request(NAME)).unwrap().position, Some(at));
            assert_eq!(snapshot(&db), before);
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn publication_rechecks_declared_against_received_totals_on_earlier_headers() {
    let db = Database::new();
    let (first, last) = example();
    send(&db.store, first).unwrap();
    let at = send(&db.store, last).unwrap().position.unwrap();
    for (key, corrupt, correct) in [("received_bytes", 1531, 1532), ("received_records", 2, 3)] {
        let update = |n| {
            db.execute(&format!(
                "UPDATE {{{{schema}}}}.record_staging
             SET body = jsonb_set(body, '{{{key}}}',to_jsonb({n}::integer)) WHERE ord=1"
            ))
        };
        update(corrupt);
        let before = snapshot(&db);
        assert_conflict(
            send(&db.store, commit(request(NAME))),
            "restore_incomplete",
            json!({"position":at.to_json()}),
        );
        assert_eq!(snapshot(&db), before);
        assert_invisible(&db);
        update(correct);
    }
    assert_eq!(
        send(&db.store, commit(request(NAME)))
            .unwrap()
            .digest
            .as_deref(),
        Some(DIGEST)
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_legacy_write_between_parts_preserves_its_own_data_and_the_unpublished_restore() {
    for projection in [false, true] {
        let db = Database::new();
        let (first, last) = example();
        send(&db.store, first).unwrap();
        let before = snapshot(&db);
        let other = db.another_store();
        if projection {
            other.append_projections(ID, &last.projections).unwrap();
        } else {
            other
                .append_turn(ID, vec![message("user", "original")], None)
                .unwrap();
        }
        let visible = [
            "conversations",
            "turns",
            "projections",
            "turn_redactions",
            "record_turns",
        ]
        .map(|table| rows(&db, table));
        assert_conflict(send(&db.store, commit(last)), "target_occupied", json!({}));
        assert_eq!(snapshot(&db), before);
        assert_eq!(
            [
                "conversations",
                "turns",
                "projections",
                "turn_redactions",
                "record_turns"
            ]
            .map(|table| rows(&db, table)),
            visible
        );
        assert_eq!(send(&other, request(NAME)).unwrap().state, Staged::Open);
    }
}
