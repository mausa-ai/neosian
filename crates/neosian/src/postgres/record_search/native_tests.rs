//! Indexed membership and integrity of the records a winning hit uses.
use super::{Order, statement};
use crate::postgres::records_conformance::Database;
use serde_json::Value;

fn index_names<'a>(plan: &'a Value, found: &mut Vec<&'a str>) {
    if let Some(name) = plan["Index Name"].as_str() {
        found.push(name);
    }
    if let Some(children) = plan["Plans"].as_array() {
        for child in children {
            index_names(child, found);
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN with pg_trgm available"]
fn operator_trigram_indexes_find_both_text_and_name() {
    let db = Database::new();
    // The operator changes, including pg_trgm if first installed here, are
    // scoped to this transaction. Fill enough rows for selective indexed
    // access to win under the planner's ordinary cost settings.
    db.execute("
        BEGIN;
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        INSERT INTO {{schema}}.conversations SELECT 'c-' || c, now() FROM generate_series(1,256) c;
        INSERT INTO {{schema}}.turns (conversation_id, turn, messages, created_at, neosian_format)
            SELECT conversation_id, 1, '[]', created_at, 2 FROM {{schema}}.conversations;
        INSERT INTO {{schema}}.record_turns (conversation_id, turn, owner, part, draft, records, bytes, created_at)
            SELECT 'c-' || c, 1, c, 0, 'test', 256, 65536, now() FROM generate_series(1,256) c;
        INSERT INTO {{schema}}.turn_records
            (conversation_id, owner, part, seq, message, role, kind, name, chunk, size, last, text, digest)
        SELECT 'c-' || c, c, 0, s, s,
            CASE WHEN s % 16 = 15 THEN 'assistant' ELSE 'user' END,
            CASE WHEN s % 16 = 15 THEN 'tool_call' ELSE 'prompt' END,
            CASE WHEN c=103 AND s=63 THEN 'qzx' WHEN s % 16=15 THEN 'helper' END,
            0, 256, true,
            CASE WHEN c=71 AND s=128 THEN 'qzx' ELSE repeat(md5((c*256+s)::text),8) END, decode('00','hex')
        FROM generate_series(1,256) c CROSS JOIN generate_series(0,255) s;
        CREATE INDEX records_plan_text ON {{schema}}.turn_records USING gin (text gin_trgm_ops);
        CREATE INDEX records_plan_name ON {{schema}}.turn_records USING gin (name gin_trgm_ops);
        ANALYZE {{schema}}.turns; ANALYZE {{schema}}.record_turns; ANALYZE {{schema}}.turn_records;
    ");
    for order in [Order::Newest, Order::Relevance] {
        let label = order.name();
        let query = statement("{{schema}}", 1, order);
        db.execute(&format!("PREPARE plan_{label} AS {query}"));
        let limit = if order == Order::Newest { "20" } else { "NULL" };
        let execute = format!(
            "EXECUTE plan_{label}(NULL, ARRAY['prompt','reply','system','tool_result','tool_call'], NULL, NULL, {limit}, '%qzx%')"
        );
        let plan: Value = db.query(&format!(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {execute}"
        ))[0]
            .get(0);
        let tree = &plan[0]["Plan"];
        let mut indexes = Vec::new();
        index_names(tree, &mut indexes);
        assert_eq!(tree["Actual Rows"], 2);
        for index in ["records_plan_text", "records_plan_name"] {
            assert!(indexes.contains(&index), "{label}: missing {index}; {plan}");
        }
        let keys: Vec<(String, i32)> = db
            .query(&execute)
            .iter()
            .map(|r| (r.get("conversation_id"), r.get("seq")))
            .collect();
        assert!(keys.contains(&("c-71".into(), 128)));
        assert!(keys.contains(&("c-103".into(), 63)));
        println!(
            "{label}: {} rows; indexes {indexes:?}; shared blocks hit {}, read {}; execution {} ms",
            tree["Actual Rows"],
            tree["Shared Hit Blocks"],
            tree["Shared Read Blocks"],
            plan[0]["Execution Time"]
        );
        println!("{label} EXPLAIN {plan}");
    }
    db.execute("ROLLBACK");
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn tool_result_refuses_an_unreadable_referenced_call() {
    use crate::conversation::message;
    use crate::conversation::turn_records::{Kind, RecordSearch, Write, records_of};
    use crate::limits::Records;
    use serde_json::json;

    let db = Database::new();
    let messages = [
        json!({"role":"assistant", "tool_calls":[{"id":"id-1", "name":"original", "arguments":{}}]}),
        json!({"role":"tool", "tool_call_id":"id-1", "content":"linked"}),
        json!({"role":"tool", "tool_call_id":"orphan", "content":"unrelated"}),
    ].iter().map(|m| message::from_json(m).unwrap()).collect::<Vec<_>>();
    let records = records_of(&messages).unwrap();
    let seq = records
        .iter()
        .find(|r| r.kind == Kind::ToolCall)
        .unwrap()
        .seq;
    db.store
        .write_records(
            "refs",
            Write {
                draft: "call".into(),
                records,
                seal: true,
                ..Default::default()
            },
            &Records::load().unwrap(),
        )
        .unwrap();
    let search = |term, order| {
        db.store.search_records(
            &RecordSearch::new(term, Some(vec![Kind::ToolResult]), order, None, None, 5).unwrap(),
        )
    };
    assert_eq!(
        search("linked", Order::Newest).unwrap()[0].name.as_deref(),
        Some("original")
    );
    // The body is still a legal call, but its name no longer agrees with the digest.
    db.execute("UPDATE {{schema}}.turn_records SET name='changed' WHERE kind='tool_call'");
    for order in [Order::Newest, Order::Relevance] {
        let error = search("linked", order).unwrap_err().text();
        assert!(
            error.contains("agent_conversation_format_unsupported"),
            "{error}"
        );
        assert!(
            error.contains(&format!("record {seq} is unreadable")),
            "{error}"
        );
        assert!(search("unrelated", order).unwrap()[0].name.is_none());
    }
}
