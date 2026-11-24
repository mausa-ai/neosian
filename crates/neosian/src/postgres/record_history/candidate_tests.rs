//! Database plans and membership for the two native history predicate shapes.
use super::{EXPANDED, Order, statement};
use crate::postgres::records_conformance::Database;
use serde_json::Value;
use std::collections::BTreeSet;

fn fixture(db: &Database) {
    // Roll back the operator additions, including pg_trgm on a database
    // where the operator had not installed it before this test.
    db.execute("
        BEGIN;
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        INSERT INTO {{schema}}.conversations
            SELECT 'v' || lpad(c::text, 3, '0'), now() FROM generate_series(1,256) c;
        INSERT INTO {{schema}}.turns (conversation_id,turn,messages,created_at,neosian_format)
            SELECT conversation_id,1,'[]',created_at,2 FROM {{schema}}.conversations;
        INSERT INTO {{schema}}.record_turns (conversation_id,turn,owner,part,draft,records,bytes,created_at)
            SELECT 'v' || lpad(c::text,3,'0'),1,c,0,'test',256,32768,now() FROM generate_series(1,256) c;
        INSERT INTO {{schema}}.turn_records
            (conversation_id,owner,part,seq,message,role,kind,name,chunk,size,last,text,digest)
        SELECT 'v' || lpad(c::text,3,'0'),c,0,s,s,
            CASE WHEN s=200 AND c=3 THEN 'assistant' ELSE 'user' END,
            CASE WHEN s=200 AND c=3 THEN 'tool_call' ELSE 'prompt' END,
            CASE WHEN s=200 AND c=3 THEN 'zzz' END,
            0,128,true,
            CASE WHEN c IN (1,2) AND s<16 THEN 'word' || lpad(s::text,3,'0')
                 WHEN c=1 AND s=200 THEN 'zzz' ELSE repeat(md5((256*c+s)::text),4) END,
            decode('00','hex')
        FROM generate_series(1,256) c CROSS JOIN generate_series(0,255) s;
        INSERT INTO {{schema}}.conversations VALUES ('legacy-all',now()), ('legacy-first',now());
        INSERT INTO {{schema}}.turns (conversation_id,turn,messages,created_at,neosian_format,search_text)
            SELECT conversation_id,1,'[]',created_at,1,words || CASE WHEN conversation_id='legacy-all' THEN ' zzz' ELSE '' END
            FROM {{schema}}.conversations CROSS JOIN (
                SELECT string_agg('word' || lpad(s::text,3,'0'), ' ' ORDER BY s) AS words FROM generate_series(0,15) s
            ) terms WHERE conversation_id LIKE 'legacy-%';
        CREATE INDEX history_plan_text ON {{schema}}.turn_records USING gin (text gin_trgm_ops);
        CREATE INDEX history_plan_name ON {{schema}}.turn_records USING gin (name gin_trgm_ops);
        ANALYZE {{schema}}.turns; ANALYZE {{schema}}.record_turns; ANALYZE {{schema}}.turn_records;
    ");
}

fn indexes<'a>(node: &'a Value, found: &mut BTreeSet<&'a str>) {
    if let Some(name) = node["Index Name"].as_str() {
        found.insert(name);
    }
    if let Some(children) = node["Plans"].as_array() {
        for child in children {
            indexes(child, found);
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN with pg_trgm available"]
fn actual_history_predicates_find_a_decisive_seventeenth_term() {
    let db = Database::new();
    fixture(&db);
    for scope in ["all", "named"] {
        let ids = if scope == "all" {
            "NULL"
        } else {
            "ARRAY['v001','v002','v003','legacy-all','legacy-first']"
        };
        for order in [Order::Newest, Order::Relevance] {
            let limit = if order == Order::Newest {
                "100"
            } else {
                "NULL"
            };
            for count in [1_usize, 16, 17] {
                let mut terms: Vec<_> = (0..count.min(16))
                    .map(|n| format!("'%word{n:03}%'"))
                    .collect();
                if count == 1 {
                    terms.clear();
                }
                if count == 1 || count == 17 {
                    terms.push("'%zzz%'".to_owned());
                }
                let terms = terms.join(",");
                let tail = if count <= EXPANDED {
                    format!(",{terms}")
                } else {
                    String::new()
                };
                let name = format!("h_{scope}_{}_{count}", order.name());
                let query = statement("{{schema}}", order, count, scope == "named");
                db.execute(&format!("PREPARE {name} AS {query}"));
                let execute = format!(
                    "EXECUTE {name}(ARRAY[{terms}],{ids},NULL,NULL,NULL,NULL,NULL,{limit}{tail})"
                );
                let plan: Value = db.query(&format!(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {execute}"
                ))[0]
                    .get(0);
                let root = &plan[0]["Plan"];
                let mut chosen = BTreeSet::new();
                indexes(root, &mut chosen);
                let rows = db.query(&execute);
                let got: BTreeSet<&str> = rows.iter().map(|r| r.get("conversation_id")).collect();
                let eligible: &[&str] = match (count, order) {
                    (1, _) => &["legacy-all", "v001", "v003"],
                    (16, _) => &["legacy-all", "legacy-first", "v001", "v002"],
                    (17, Order::Newest) => &["legacy-all", "v001"],
                    (17, Order::Relevance) => {
                        &["legacy-all", "legacy-first", "v001", "v002", "v003"]
                    }
                    _ => unreachable!(),
                };
                assert_eq!(got, eligible.iter().copied().collect(), "{name}");
                assert_eq!(rows.len(), got.len(), "duplicate candidate turn in {name}");
                if scope == "all" && count == 1 {
                    assert!(
                        chosen.contains("history_plan_text")
                            && chosen.contains("history_plan_name"),
                        "{name} {chosen:?}"
                    );
                }
                println!(
                    "{name} results={got:?} indexes={chosen:?} hit_blocks={} exec_ms={} plan_ms={}",
                    root["Shared Hit Blocks"], plan[0]["Execution Time"], plan[0]["Planning Time"]
                );
                println!("{name} EXPLAIN {plan}");
            }
        }
    }
    db.execute("ROLLBACK");
}
