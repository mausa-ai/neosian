//! The grants table for the turn-records routes (DESIGN §40.54 item 8.2):
//! `write_records` needs append on its conversation, `read_records` read,
//! `search_records` read on every conversation it lists, and the sweep the
//! operator.

use serde_json::{Value, json};

use super::Grants;

const TABLE: &str = r#"
grant_format = 1
[[clients]]
actor = "client:reader"
[[clients.conversations]]
prefix = "s-"
read = true
[[clients]]
actor = "client:writer"
[[clients.conversations]]
ids = ["s-1"]
append = true
[[clients]]
actor = "client:both"
[[clients.conversations]]
prefix = "s-"
read = true
append = true
"#;

fn refused(grants: &Grants, actor: &str, route: &str, body: Value) -> bool {
    let payload = body.as_object().expect("an object");
    let refusal = grants.allowed(actor, route, payload, false);
    if let Some(fail) = &refusal {
        assert_eq!((fail.status.as_u16(), fail.code), (403, "forbidden"));
    }
    refusal.is_some()
}

#[test]
fn write_records_needs_append_on_its_conversation() {
    let grants: Grants = toml::from_str(TABLE).unwrap();
    let route = "conversation/write_records";
    let body = |id: &str| json!({"conversation_id": id, "draft": "d", "records": []});
    assert!(refused(&grants, "client:reader", route, body("s-1")));
    assert!(!refused(&grants, "client:writer", route, body("s-1")));
    assert!(!refused(&grants, "client:both", route, body("s-2")));
    assert!(refused(&grants, "client:writer", route, body("s-2")));
    assert!(refused(&grants, "client:both", route, body("t-1")));
    assert!(refused(&grants, "client:unknown", route, body("s-1")));
    assert!(refused(
        &grants,
        "client:both",
        route,
        json!({"draft": "d"})
    ));
    assert!(refused(
        &grants,
        "client:both",
        route,
        json!({"conversation_id": 7})
    ));
    // The legacy routes keep their side of the table.
    let read = "conversation/read_turns";
    assert!(!refused(&grants, "client:reader", read, body("s-1")));
    assert!(refused(&grants, "client:writer", read, body("s-1")));
    let append = "conversation/append_turn";
    assert!(refused(&grants, "client:reader", append, body("s-1")));
    assert!(!refused(&grants, "client:writer", append, body("s-1")));
}

#[test]
fn read_records_needs_read_on_its_conversation() {
    let grants: Grants = toml::from_str(TABLE).unwrap();
    let route = "conversation/read_records";
    let body = |id: &str| json!({"conversation_id": id});
    assert!(!refused(&grants, "client:reader", route, body("s-1")));
    assert!(!refused(&grants, "client:both", route, body("s-9")));
    assert!(refused(&grants, "client:writer", route, body("s-1")));
    assert!(refused(&grants, "client:reader", route, body("t-1")));
    assert!(refused(&grants, "client:unknown", route, body("s-1")));
    // Nothing else in the body stands in for the conversation named.
    for body in [
        json!({}),
        json!({"conversation_id": 7}),
        json!({"conversations": ["s-1"]}),
        json!({"conversation_id": "t-1", "conversations": ["s-1"]}),
        json!({"conversation_id": "t-1", "continuation": "s-1"}),
    ] {
        assert!(refused(&grants, "client:reader", route, body));
    }
}

#[test]
fn search_records_needs_read_on_every_conversation_it_lists() {
    let grants: Grants = toml::from_str(TABLE).unwrap();
    let route = "conversation/search_records";
    let body = |ids: &[&str]| json!({"query": "x", "conversations": ids});
    assert!(!refused(&grants, "client:reader", route, body(&["s-1"])));
    assert!(!refused(
        &grants,
        "client:both",
        route,
        body(&["s-1", "s-9"])
    ));
    // An empty list reads nothing and is allowed to a client with a grant.
    assert!(!refused(&grants, "client:reader", route, body(&[])));
    assert!(!refused(&grants, "client:writer", route, body(&[])));
    assert!(refused(&grants, "client:unknown", route, body(&[])));
    assert!(refused(&grants, "client:writer", route, body(&["s-1"])));
    assert!(refused(
        &grants,
        "client:reader",
        route,
        body(&["s-1", "t-1"])
    ));
    // Absent, or named any other way, is denied to every actor (as the
    // list branch denies `search_turns`).
    for actor in ["client:reader", "client:both"] {
        for body in [
            json!({"query": "x"}),
            json!({"query": "x", "conversations": null}),
            json!({"query": "x", "conversations": "s-1"}),
            json!({"query": "x", "conversations": ["s-1", 7]}),
            json!({"query": "x", "conversation_id": "s-1"}),
        ] {
            assert!(refused(&grants, actor, route, body));
        }
    }
}

#[test]
fn sweep_records_is_refused_to_every_grant_but_the_operators() {
    let table = format!("{TABLE}[[clients]]\nactor = \"client:ops\"\noperator = true\n");
    let grants: Grants = toml::from_str(&table).unwrap();
    let whole = |actor: &str, body: Value| {
        let payload = body.as_object().expect("an object");
        let refusal = grants.allowed(actor, "store/sweep_records", payload, true);
        refusal.is_some()
    };
    assert!(!whole("client:ops", json!({})));
    assert!(!whole("client:ops", json!({"write": true})));
    for actor in [
        "client:reader",
        "client:writer",
        "client:both",
        "client:unknown",
    ] {
        assert!(whole(actor, json!({})));
        assert!(whole(actor, json!({"conversation_id": "s-1"})));
        assert!(whole(actor, json!({"conversations": ["s-1"]})));
    }
}
