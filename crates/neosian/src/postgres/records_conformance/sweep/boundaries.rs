//! Input validation precedes deletion; a page never clips the sum of open owners' ledgers.
use super::*;
use crate::conversation::Error;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn invalid_page_parameters_preserve_the_eligible_rows_and_match_file_errors() {
    let p = Pair::new();
    let bytes = p.write("stale", "one", false);
    p.stale("stale", "write", "one");
    let before = snapshot(&p.db);
    let too_long = "a".repeat(4097);
    let wrong_inputs = [
        (0, Some("!")),
        (101, None),
        (usize::MAX, None),
        (1, Some("")),
        (100, Some(too_long.as_str())),
        (1, Some("*")),
        (1, Some("WyJ3cm9uZyIsIm9rIl0=")),         // wrong method
        (1, Some("WyJzd2VlcC12MSJd")),             // missing field
        (1, Some("WyJzd2VlcC12MSIsIm9rIiwieCJd")), // extra field
        (1, Some("WyJzd2VlcC12MSIsMV0=")),         // integer ID
        (1, Some("WyJzd2VlcC12MSIsImJhZC9pZCJd")), // invalid flat ID
        (1, Some("WyJzd2VlcC12MSIsIi4uIl0=")),     // reserved ID
    ];
    for (limit, cursor) in wrong_inputs {
        for apply in [false, true] {
            let pg = p.db.store.sweep_records(limit, cursor, apply).unwrap_err();
            let file = p.file.sweep_records(limit, cursor, apply).unwrap_err();
            assert!(matches!(pg, Error::Value(_)));
            assert_eq!(pg.text(), file.text());
            if limit == 0 {
                assert!(pg.text().contains("sweep limit"));
            }
        }
    }
    assert_eq!(snapshot(&p.db), before);
    p.check_page(
        1,
        None,
        None,
        page(&[row("stale", 1, 0, bytes)], None, true),
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn held_bytes_allow_the_full_unsigned_total_and_reject_negative_or_overflowing_ledgers() {
    let db = Database::new();
    for name in ["a", "b", "c"] {
        db.store
            .write_records("ledger", write(name, Some("small"), false), &LIMITS)
            .unwrap();
    }
    db.execute(
        "UPDATE {{schema}}.record_drafts SET touched_at='2000-01-01T00:00:00Z',
                bytes=9223372036854775807",
    );
    for bad in [-1, 2] {
        // max_i64 + max_i64 - 1 fits in u64, but one corrupt negative owner must still refuse.
        db.execute(&format!(
            "UPDATE {{{{schema}}}}.record_drafts SET bytes={bad} WHERE name='c'"
        ));
        let before = snapshot(&db);
        for apply in [false, true] {
            let refused = db.store.sweep_records(1, None, apply).unwrap_err();
            assert!(
                matches!(refused,Error::Conversation(ref e) if e.code=="agent_conversation_format_unsupported"),
                "{refused:?}"
            );
            assert_eq!(snapshot(&db), before);
        }
    }
    db.execute("UPDATE {{schema}}.record_drafts SET bytes=1 WHERE name='c'");
    for apply in [false, true] {
        let result = db.store.sweep_records(1, None, apply).unwrap();
        assert_eq!(
            serde_json::to_string(&result).unwrap(),
            page(&[row("ledger", 3, 0, u64::MAX)], None, apply).to_string()
        );
    }
    assert!(rows(&db, "turn_records").is_empty());
    assert!(rows(&db, "record_drafts").is_empty());
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn the_hundredth_eligible_key_has_a_cursor_only_while_another_key_exists() {
    let db = Database::new();
    // Real admission for each equal-length ID gives every row an independently observed charge.
    let mut expected = Vec::new();
    for n in 0..=100 {
        let id = format!("c{n:03}");
        let admitted = db
            .store
            .write_records(&id, write("w", Some("tiny"), false), &LIMITS)
            .unwrap();
        expected.push(row(&id, 1, 0, admitted.stored_bytes));
    }
    db.execute("UPDATE {{schema}}.record_drafts SET touched_at='2000-01-01T00:00:00Z' WHERE conversation_id<>'c100'");
    let preview = db.store.sweep_records(100, None, false).unwrap();
    assert_eq!(json!(preview), page(&expected[..100], None, false));
    db.execute("UPDATE {{schema}}.record_drafts SET touched_at='2000-01-01T00:00:00Z' WHERE conversation_id='c100'");
    let first = db.store.sweep_records(100, None, true).unwrap();
    let cursor = "WyJzd2VlcC12MSIsImMwOTkiXQ==";
    assert_eq!(json!(first), page(&expected[..100], Some(cursor), true));
    let remaining = rows(&db, "record_drafts");
    assert_eq!(remaining.len(), 1);
    assert_eq!(remaining[0]["conversation_id"], "c100");
    let last = db
        .another_store()
        .sweep_records(100, Some(cursor), true)
        .unwrap();
    assert_eq!(json!(last), page(&expected[100..], None, true));
}
