//! Inspect the operator-visible records directly, independently of the read path.

use super::Database;
use crate::conversation::message;
use crate::conversation::turn_records::{Write, records_of};
use crate::limits::Records;
use serde_json::{Value, json};

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn staged_rows_preserve_the_codec_fields_digests_and_final_chunk_flags() {
    let source = [
        json!({"role":"user","content":"please run the test"}),
        json!({"role":"assistant","content":null,
            "tool_calls":[{"id":"call-1","name":"runner","arguments":{"suite":"test"}}]}),
        json!({"role":"tool","tool_call_id":"call-1","content":"\nλ".repeat(65_536)}),
        json!({"role":"assistant","content":"done"}),
    ];
    let messages: Vec<_> = source
        .iter()
        .map(|v| message::from_json(v).unwrap())
        .collect();
    let records = records_of(&messages).unwrap();
    assert!(records.iter().any(|r| r.chunk > 1));
    let db = Database::new();
    let result = db
        .store
        .write_records(
            "audit",
            Write {
                draft: "part".into(),
                records: records.clone(),
                ..Write::default()
            },
            &Records::load().unwrap(),
        )
        .unwrap();
    assert_eq!(result.next_seq as usize, records.len());
    let sql_rows = db.query("SELECT to_jsonb(r) - ARRAY['conversation_id','owner','part','last','digest','neosian_format'] AS body,
            digest, last, neosian_format,
            (r.conversation_id, r.owner, r.part) = ('audit', d.owner, 0) AS owns
        FROM {{schema}}.turn_records r CROSS JOIN
            (SELECT owner FROM {{schema}}.record_drafts WHERE conversation_id='audit' AND name='part') d
        ORDER BY seq");
    assert_eq!(sql_rows.len(), records.len());
    for (n, (row, record)) in sql_rows.iter().zip(&records).enumerate() {
        let value: Value = row.get("body");
        let expected = record.to_json(None);
        for (field, actual) in value.as_object().unwrap() {
            assert_eq!(actual, &expected[field], "record {n}, field {field}");
        }
        let digest: &[u8] = row.get("digest");
        assert_eq!(digest, record.digest(None));
        let last = records.get(n + 1).is_none_or(|next| next.chunk == 0);
        assert_eq!(row.get::<_, bool>("last"), last);
        assert_eq!(row.get::<_, i32>("neosian_format"), 1);
        assert!(row.get::<_, bool>("owns"));
    }
    assert!(db.query("SELECT 1 FROM {{schema}}.record_turns").is_empty());
}
