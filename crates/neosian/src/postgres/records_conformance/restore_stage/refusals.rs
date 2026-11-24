//! Refused restore parts are invisible even to a retry inspecting stored tuples.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_refused_mixed_part_changes_no_items_position_digest_or_touch() {
    let db = Database::new();
    let (mut first, second) = example();
    let tail = first.records.split_off(1);
    let opening = send(&db.store, first.clone()).unwrap();
    let at = Position {
        turn: 1,
        seq: 1,
        complete: false,
        projections: 0,
        redactions: 0,
    };
    assert_eq!(opening.position, Some(at));
    let before = snapshot(&db);
    let mut overlap = first.clone();
    overlap.records[0].1.text = "X".repeat(32);
    overlap.records.extend(tail.clone());
    let mut changed_header = first.clone();
    changed_header.turns[0].actor = Some("other".into());
    let mut too_short = tail.clone();
    too_short[1].1.text.pop();
    too_short[1].1.size -= 1;
    for (part, reason) in [
        (overlap, "restore_mismatch"),
        (changed_header, "restore_mismatch"),
        (
            Restore {
                records: vec![tail[1].clone()],
                ..request(NAME)
            },
            "restore_gap",
        ),
        (
            Restore {
                turns: second.turns.clone(),
                ..request(NAME)
            },
            "restore_gap",
        ),
    ] {
        assert_conflict(
            send(&db.store, part),
            reason,
            json!({"position":at.to_json()}),
        );
        assert_eq!(snapshot(&db), before);
    }
    let invalid = Restore {
        records: too_short,
        ..request(NAME)
    };
    assert!(matches!(send(&db.store, invalid), Err(Error::Value(_))));
    assert_eq!(snapshot(&db), before);
    send(
        &db.another_store(),
        Restore {
            records: tail,
            ..request(NAME)
        },
    )
    .unwrap();
    send(&db.store, second.clone()).unwrap();
    let completed = snapshot(&db);
    let pos = send(&db.store, request(NAME)).unwrap().position.unwrap();
    let mut proj = second.clone();
    proj.projections[0].text.push('!');
    let mut act = second.clone();
    act.redactions[0].actor = None;
    for wrong in [proj, act] {
        assert_conflict(
            send(&db.store, wrong),
            "restore_mismatch",
            json!({"position":pos.to_json()}),
        );
        assert_eq!(snapshot(&db), completed);
    }
    let mut past_acts = second;
    past_acts.turns.clear();
    past_acts.records.clear();
    past_acts.redactions.clear();
    past_acts.projections_from = Some(1);
    assert!(matches!(send(&db.store, past_acts), Err(Error::Value(_))));
    assert_eq!(snapshot(&db), completed);
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn staging_checkpoint_refuses_commit_before_applying_any_items() {
    let db = Database::new();
    let (first, second) = example();
    for (open, content) in [(false, first.clone()), (true, second)] {
        if open {
            send(&db.store, first.clone()).unwrap();
        }
        let before = snapshot(&db);
        for mut part in [request(NAME), content] {
            part.commit = true;
            assert!(matches!(send(&db.store, part), Err(Error::Value(sentence))
                if sentence.contains("commit")));
            assert_eq!(snapshot(&db), before);
            assert_invisible(&db);
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn header_bounds_empty_shapes_and_sql_integer_ranges_precede_storage() {
    let db = Database::new();
    let (mut first, _) = example();
    first.records.clear();
    let template = first.turns[0].clone();
    for invalid in [
        Header {
            records: 0,
            bytes: 0,
            ..template.clone()
        },
        Header {
            redacted: true,
            ..template.clone()
        },
        Header {
            bytes: LIMITS.max_record_turn_bytes + 1,
            ..template.clone()
        },
        Header {
            turn: i32::MAX as u64 + 1,
            ..template.clone()
        },
    ] {
        first.turns = vec![invalid];
        assert!(matches!(
            send(&db.store, first.clone()),
            Err(Error::Value(_))
        ));
        assert!(snapshot(&db).iter().all(Vec::is_empty));
    }
    let mut empty = records_of(&[message("user", "")]).unwrap();
    first.turns = vec![
        Header {
            records: 0,
            bytes: 0,
            redacted: true,
            ..template.clone()
        },
        Header {
            turn: 5,
            records: 1,
            bytes: 0,
            ..template
        },
    ];
    first.records = vec![(5, empty.remove(0))];
    let reply = send(&db.store, first).unwrap();
    assert_eq!(
        reply.position,
        Some(Position {
            turn: 5,
            seq: 1,
            complete: true,
            projections: 0,
            redactions: 0,
        })
    );
    assert_eq!(rows(&db, "turn_records").len(), 1);
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn sparse_turns_and_multibyte_chunks_keep_original_keys_and_piece_ends() {
    let db = Database::new();
    let mut first = request(NAME);
    let record = records_of(&[message("user", "é🙂")]).unwrap().remove(0);
    first.turns = vec![header(7, "2026-10-01T00:00:00Z", 2, 6, false)];
    first.records = vec![(
        7,
        TurnRecord {
            text: "é".into(),
            ..record.clone()
        },
    )];
    let open = send(&db.store, first.clone()).unwrap();
    assert!(!open.position.unwrap().complete);
    let old = rows(&db, "turn_records");
    assert_eq!(old[0]["last"], false);
    let mut rest = request(NAME);
    rest.records = vec![(
        7,
        TurnRecord {
            seq: 1,
            chunk: 1,
            content: None,
            text: "🙂".into(),
            ..record
        },
    )];
    rest.turns = vec![header(9, "2026-10-02T00:00:00Z", 0, 0, true)];
    let reopened = db.another_store();
    let done = send(&reopened, rest).unwrap();
    assert!(done.position.unwrap().complete);
    let fresh = rows(&db, "turn_records");
    assert!(fresh.contains(&old[0]));
    let end = fresh.iter().find(|r| r["seq"] == 1).unwrap();
    assert_eq!(
        (&end["part"], &end["last"], &end["size"]),
        (&json!(7), &json!(true), &json!(6))
    );
    let before = snapshot(&db);
    first.turns[0].turn = 8;
    first.records.clear();
    assert_conflict(
        send(&reopened, first),
        "restore_mismatch",
        json!({"position":done.position.unwrap().to_json()}),
    );
    assert_eq!(snapshot(&db), before);
    // Existence of a projection's target is a commit-time check.
    let future = Restore {
        projections_from: Some(0),
        projections: vec![Projection {
            turn: 8,
            span: 1,
            kind: "epoch".into(),
            text: "not selected for import".into(),
        }],
        ..request(NAME)
    };
    assert_eq!(
        send(&reopened, future)
            .unwrap()
            .position
            .unwrap()
            .projections,
        1
    );
    assert_invisible(&db);
}
