//! The two bodies of `record_search` as the stores receive them: every
//! field, its default when absent, and each refusal by name.

use serde_json::{Value, json};

use super::*;
use crate::conversation::turn_records::Kind;

fn body(value: Value) -> Payload {
    value.as_object().expect("an object").clone()
}

fn refused<T>(result: Result<T, Fail>) -> String {
    let fail = result.err().expect("the body is refused");
    assert_eq!((fail.status.as_u16(), fail.code), (400, "value_error"));
    fail.message
}

#[test]
fn a_search_takes_its_defaults_only_when_a_field_is_absent() {
    let (plain, named) = search(&body(json!({"query": "Beta  alpha beta"}))).unwrap();
    assert_eq!(plain.terms, ["alpha", "beta"]);
    assert_eq!(plain.conversations, None);
    assert_eq!(
        plain.kinds,
        [
            Kind::Prompt,
            Kind::Reply,
            Kind::System,
            Kind::ToolResult,
            Kind::ToolCall
        ]
    );
    assert_eq!((plain.order, named), (Order::Newest, false));
    assert_eq!((plain.bounds, plain.limit), ((None, None), 20));
    let (full, named) = search(&body(json!({
        "query": "x",
        "conversations": ["b", "a", "b"],
        "kinds": ["reply", "prompt", "reply"],
        "order": "newest",
        "since": "2026-11-21T00:00:00Z",
        "before": "2026-11-22T00:00:00.000001Z",
        "limit": 100,
    })))
    .unwrap();
    assert_eq!(full.conversations.unwrap(), ["b", "a", "b"]);
    assert_eq!(full.kinds, [Kind::Reply, Kind::Prompt]);
    // An order named is echoed, also when it is the default.
    assert_eq!((full.order, named), (Order::Newest, true));
    assert_eq!(
        full.bounds,
        (Some(1_795_219_200_000_000), Some(1_795_305_600_000_001))
    );
    assert_eq!(full.limit, 100);
    let (ranked, named) = search(&body(json!({"query": "x", "order": "relevance"}))).unwrap();
    assert_eq!((ranked.order, named), (Order::Relevance, true));
    let none = search(&body(
        json!({"query": "x", "conversations": [], "kinds": []}),
    ));
    let (none, _) = none.unwrap();
    assert_eq!(none.conversations.unwrap(), Vec::<String>::new());
    assert!(none.kinds.is_empty());
}

#[test]
fn every_rule_of_a_search_refuses_by_name() {
    let message = |value: Value| refused(search(&body(value)));
    let query = "query requires 1..1024 characters and 1..16 whitespace terms";
    for (value, why) in [
        (json!({}), "missing parameter: 'query'"),
        (
            json!({"query": "x", "page_bytes": 1}),
            "unknown parameter: 'page_bytes'",
        ),
        (
            json!({"query": "x", "conversation_id": "a"}),
            "unknown parameter: 'conversation_id'",
        ),
        (
            json!({"query": "x", "cursor": "a"}),
            "unknown parameter: 'cursor'",
        ),
        (json!({"query": null}), "parameter 'query' must be a string"),
        (
            json!({"query": ["x"]}),
            "parameter 'query' must be a string",
        ),
        (json!({"query": ""}), query),
        (json!({"query": " \t\n"}), query),
        (json!({"query": "x".repeat(1_025)}), query),
        (json!({"query": "x ".repeat(17)}), query),
        (
            json!({"query": "x", "conversations": null}),
            "parameter 'conversations' must be a list",
        ),
        (
            json!({"query": "x", "conversations": "a"}),
            "parameter 'conversations' must be a list",
        ),
        (
            json!({"query": "x", "conversations": ["a", 7]}),
            "parameter 'conversations' must be a list of ids",
        ),
        (
            json!({"query": "x", "kinds": null}),
            "parameter 'kinds' must be a list",
        ),
        (
            json!({"query": "x", "kinds": [7]}),
            "parameter 'kinds' must be a list of kinds",
        ),
        (
            json!({"query": "x", "kinds": ["prompts"]}),
            "unknown record kind \"prompts\"",
        ),
        (
            json!({"query": "x", "kinds": ["reasoning"]}),
            "kind \"reasoning\" is not searchable",
        ),
        (
            json!({"query": "x", "kinds": ["prompt", "extra"]}),
            "kind \"extra\" is not searchable",
        ),
        (
            json!({"query": "x", "kinds": ["attachment"]}),
            "kind \"attachment\" is not searchable",
        ),
        (
            json!({"query": "x", "order": null}),
            "parameter 'order' must be a string",
        ),
        (
            json!({"query": "x", "order": "path"}),
            "order must be newest or relevance",
        ),
        (
            json!({"query": "x", "order": "Newest"}),
            "order must be newest or relevance",
        ),
        (
            json!({"query": "x", "since": null}),
            "parameter 'since' must be a string",
        ),
        (
            json!({"query": "x", "before": 7}),
            "parameter 'before' must be a string",
        ),
        (
            json!({"query": "x", "since": "2026-11-21T00:00:00+00:00"}),
            "since must be a UTC RFC 3339 instant ending in Z",
        ),
        (
            json!({"query": "x", "before": "yesterday"}),
            "before must be a UTC RFC 3339 instant ending in Z",
        ),
        (
            json!({"query": "x", "since": "2026-11-21T00:00:00Z", "before": "2026-11-21T00:00:00Z"}),
            "since must precede before",
        ),
        (
            json!({"query": "x", "limit": null}),
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            json!({"query": "x", "limit": "5"}),
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            json!({"query": "x", "limit": 0}),
            "limit must be between 1 and 100",
        ),
        (
            json!({"query": "x", "limit": 101}),
            "limit must be between 1 and 100",
        ),
        (
            json!({"query": "x", "limit": u64::MAX}),
            "limit must be between 1 and 100",
        ),
    ] {
        assert_eq!(message(value.clone()), why, "{value}");
    }
    assert!(search(&body(json!({"query": "x".repeat(1_024), "limit": 1}))).is_ok());
    assert!(search(&body(json!({"query": "x ".repeat(16)}))).is_ok());
}

#[test]
fn a_sweep_takes_its_defaults_and_refuses_what_it_does_not_know() {
    assert_eq!(sweep(&body(json!({}))).unwrap(), (50, None, false));
    let named = body(json!({"limit": 2, "cursor": "abc", "write": true}));
    assert_eq!(sweep(&named).unwrap(), (2, Some("abc"), true));
    // The stores refuse a count outside 1..100; the route hands it on.
    let far = body(json!({"limit": u64::MAX, "write": false}));
    assert!(sweep(&far).unwrap().0 > 100);
    for (value, why) in [
        (
            json!({"conversation_id": "a"}),
            "unknown parameter: 'conversation_id'",
        ),
        (
            json!({"conversations": []}),
            "unknown parameter: 'conversations'",
        ),
        (json!({"discard": true}), "unknown parameter: 'discard'"),
        (
            json!({"limit": null}),
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            json!({"limit": 1.5}),
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            json!({"cursor": null}),
            "parameter 'cursor' must be a string",
        ),
        (json!({"cursor": 7}), "parameter 'cursor' must be a string"),
        (
            json!({"write": null}),
            "parameter 'write' must be a boolean",
        ),
        (json!({"write": 1}), "parameter 'write' must be a boolean"),
        (
            json!({"write": "true"}),
            "parameter 'write' must be a boolean",
        ),
    ] {
        assert_eq!(refused(sweep(&body(value.clone()))), why, "{value}");
    }
}
