//! Observe physical row writes from a separate connection, across the seal.

use super::Database;
use crate::conversation::message;
use crate::conversation::turn_records::{Write, records_of};
use crate::limits::Records;
use serde_json::json;

const LIMITS: Records = Records {
    draft_seconds: 86_400,
    max_turn_bytes: 64 << 20,
    max_record_turn_bytes: 1 << 30,
};

fn seal_changes(text_bytes: usize) -> Vec<String> {
    let db = Database::new();
    let message =
        message::from_json(&json!({"role":"user","content":"x".repeat(text_bytes)})).unwrap();
    let records = records_of(&[message]).unwrap();
    let write = || Write {
        draft: "one".into(),
        ..Write::default()
    };
    for batch in records.chunks(8) {
        db.store
            .write_records(
                "s",
                Write {
                    records: batch.to_vec(),
                    ..write()
                },
                &LIMITS,
            )
            .unwrap();
    }
    assert_eq!(db.store.last_turn_number("s").unwrap(), 0);
    assert!(db.store.conversations().unwrap().is_empty());
    let before = db.record_addresses();
    assert_eq!(before.len(), records.len());
    assert!(before.iter().all(|r| r.0 == before[0].0 && r.1 == 0));
    db.observe_changes();

    let sealed = db
        .store
        .write_records(
            "s",
            Write {
                seal: true,
                ..write()
            },
            &LIMITS,
        )
        .unwrap();
    let header = sealed.turn.unwrap();
    assert_eq!(header.records, records.len() as u64);
    assert_eq!(db.store.last_turn_number("s").unwrap(), 1);
    assert_eq!(db.store.conversations().unwrap(), ["s"]);
    assert_eq!(db.record_addresses(), before);
    let rows = db.query(
        "SELECT m.owner, m.part, m.records, m.bytes FROM {{schema}}.record_turns m
        JOIN {{schema}}.turns t USING (conversation_id,turn) WHERE t.neosian_format = 2
        AND t.messages = '[]'::jsonb AND t.search_text IS NULL AND m.turn = 1 AND m.draft = 'one'",
    );
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get::<_, i64>(0), before[0].0);
    assert_eq!(rows[0].get::<_, i32>(1), 0);
    assert_eq!(rows[0].get::<_, i32>(2) as usize, records.len());
    assert_eq!(rows[0].get::<_, i64>(3) as u64, header.bytes);
    db.changes()
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_one_kib_and_a_sixteen_mib_seal_change_the_same_four_rows() {
    let expected = [
        "conversations INSERT",
        "record_drafts DELETE",
        "record_turns INSERT",
        "turns INSERT",
    ];
    assert_eq!(seal_changes(1024), expected);
    assert_eq!(seal_changes(16 << 20), expected);
}
