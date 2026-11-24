//! Query arities share a connection. The last term is decisive for each
//! newest request; a single occurrence is sufficient under relevance.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn all_sixteen_parameter_shapes_keep_their_membership_on_both_formats() {
    let db = Database::new();
    let words: Vec<_> = (1..=16).map(|n| format!("word{n:02}")).collect();
    let data: Vec<_> = (1..=16)
        .map(|n| json!({"role":"user","content":words[..n].join(" ")}))
        .collect();
    restore(&db, "legacy", T0, &data);
    write(&db, "native", "done", &data, true);
    for n in (1..=16).rev() {
        let terms = words[..n].join(" ");
        for order in [Order::Relevance, Order::Newest] {
            let hits = db.store.search_records(&query(&terms, order)).unwrap();
            let first = if order == Order::Newest { n - 1 } else { 0 };
            let expected: Vec<u64> = (first as u64..16).collect();
            for id in ["native", "legacy"] {
                let mut actual: Vec<_> = hits
                    .iter()
                    .filter(|h| h.conversation_id == id)
                    .map(|h| h.seq)
                    .collect();
                actual.sort_unstable();
                assert_eq!(actual, expected, "{n} terms in {id}, {order:?}");
            }
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn an_unconvertible_legacy_call_and_a_malformed_native_header_identify_the_turn() {
    let db = Database::new();
    let name = "x".repeat(1025);
    restore(
        &db,
        "archive",
        T0,
        &[json!({"role":"assistant","tool_calls":[
            {"id":"call","name":name,"arguments":{"word":"needle"}}
        ]})],
    );
    write(
        &db,
        "stored",
        "done",
        &[json!({"role":"user","content":"needle"})],
        true,
    );
    db.execute("UPDATE {{schema}}.turns SET messages = '[{}]' WHERE neosian_format = 2");
    for order in [Order::Newest, Order::Relevance] {
        for (id, reason) in [
            ("archive", "1024 bytes"),
            ("stored", "invalid record mapping"),
        ] {
            let mut request = query("needle", order);
            request.conversations = Some(vec![id.into()]);
            let error = db.store.search_records(&request).unwrap_err().text();
            for part in [id, "turn 1", reason] {
                assert!(error.contains(part), "{error}");
            }
        }
    }
}
