//! An extension-1 fold cannot retire a native seal's only retry evidence.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn native_seal_preserves_its_original_ranges_and_pending_work_on_an_extension_one_fold() {
    let db = Database::new();
    let old = message::from_json(&json!({"role":"user","content":"earlier"})).unwrap();
    db.store.append_turn("c", vec![old], None).unwrap();
    assert_eq!(db.store.migrate_records("c", &LIMITS).unwrap().migrated, 1);

    let msg = message::from_json(&json!({"role":"user","content":"hello world"})).unwrap();
    let mut canonical = records_of(&[msg]).unwrap();
    let original = canonical.remove(0);
    let first = TurnRecord {
        text: "hello ".into(),
        ..original.clone()
    };
    let last = TurnRecord {
        seq: 1,
        chunk: 1,
        content: None,
        text: "world".into(),
        ..original.clone()
    };
    let pieces = [first, last];
    let sealed = send(
        &db.store,
        Write {
            actor: Some("agent:writer".into()),
            turn_bytes: Some(11),
            seal: true,
            ..part("native", &pieces)
        },
    )
    .unwrap();
    assert_eq!(sealed.turn.as_ref().unwrap().turn, 2);
    send(&db.store, part("pending", &pieces[..1])).unwrap();

    let result = db
        .store
        .fold_records("c", true, &LIMITS)
        .unwrap_err()
        .text();
    assert!(
        result.contains("turn 2 of c") && result.contains("native seal"),
        "{result}"
    );
    let cold = db.another_store();
    let formats: Vec<i32> = db
        .query("SELECT neosian_format FROM {{schema}}.turns ORDER BY turn")
        .iter()
        .map(|r| r.get(0))
        .collect();
    assert_eq!(formats, [2, 2]);
    let pending = send(&cold, part("pending", &[])).unwrap();
    assert_eq!((pending.state, pending.next_seq), (State::Open, 1));
    for range in [&pieces[..0], &pieces[..1], &pieces[1..], &pieces[..]] {
        let reply = send(&cold, part("native", range)).unwrap();
        assert_eq!(reply.turn, sealed.turn);
        assert_eq!(
            (
                reply.state,
                reply.next_seq,
                reply.stored_bytes,
                reply.freed_bytes
            ),
            (State::Sealed, 2, 0, 0)
        );
    }
    let changed = conflict(send(&cold, part("native", &[original])), "draft_mismatch");
    assert_eq!(changed.details["seq"], 0);
    assert_eq!(cold.last_turn_number("c").unwrap(), 2);
}
