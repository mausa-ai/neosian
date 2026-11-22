//! A record search as every store takes it (§40.54 item 11): the request's
//! rules, what a record is matched on and counted by, and its excerpt by
//! memory search's rule, worked out by hand.

use crate::conversation::message::Role;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    Form, Kind, RecordSearch, SEARCHABLE, TurnRecord, record_excerpt, record_facts,
};

fn record(kind: Kind, name: Option<&str>, text: &str) -> TurnRecord {
    TurnRecord {
        seq: 0,
        message: 0,
        role: Role::Assistant,
        kind,
        content: Some(Form::None),
        block: None,
        call: name.map(|_| 0),
        call_id: name.map(|_| "c".to_string()),
        name: name.map(str::to_string),
        chunk: 0,
        size: text.len() as u64,
        text: text.into(),
    }
}

#[test]
fn a_request_holds_item_11s_rules() {
    let search =
        RecordSearch::new("Alpha  BETA alpha", None, Order::Newest, None, None, 20).unwrap();
    assert_eq!(search.terms, ["alpha", "beta"]);
    assert_eq!(search.kinds, SEARCHABLE);
    assert_eq!(
        SEARCHABLE.map(Kind::as_str),
        ["prompt", "reply", "system", "tool_result", "tool_call"]
    );
    let refused = |query: &str, kinds: Option<Vec<Kind>>, limit: usize| {
        RecordSearch::new(query, kinds, Order::Relevance, None, None, limit).unwrap_err()
    };
    let bounds = "query requires 1..1024 characters and 1..16 whitespace terms";
    assert_eq!(refused("", None, 20), bounds);
    assert_eq!(refused("   ", None, 20), bounds);
    assert_eq!(refused(&"x".repeat(1_025), None, 20), bounds);
    assert_eq!(refused(&"x ".repeat(17), None, 20), bounds);
    assert!(RecordSearch::new(&"x ".repeat(16), None, Order::Newest, None, None, 1).is_ok());
    for kind in [Kind::Reasoning, Kind::Attachment, Kind::Extra] {
        assert_eq!(
            refused("x", Some(vec![Kind::Prompt, kind]), 20),
            format!("kind {:?} is not searchable", kind.as_str())
        );
    }
    assert_eq!(refused("x", None, 0), "limit must be between 1 and 100");
    assert_eq!(refused("x", None, 101), "limit must be between 1 and 100");
    let kinds = Some(vec![Kind::ToolResult, Kind::Prompt, Kind::ToolResult]);
    let search = RecordSearch::new("x", kinds, Order::Newest, None, None, 100).unwrap();
    assert_eq!(search.kinds, [Kind::ToolResult, Kind::Prompt]);
    let at = |text: &str| Some(text.to_string());
    let windowed = RecordSearch::new(
        "x",
        None,
        Order::Newest,
        at("2026-11-21T10:00:00Z"),
        at("2026-11-21T10:00:01Z"),
        5,
    )
    .unwrap();
    assert_eq!(
        windowed.bounds,
        (Some(1_795_255_200_000_000), Some(1_795_255_201_000_000))
    );
    for (since, before, why) in [
        (
            at("2026-11-21T10:00:01Z"),
            at("2026-11-21T10:00:00Z"),
            "since must precede before",
        ),
        (
            at("2026-11-21T10:00:00+00:00"),
            None,
            "since must be a UTC RFC 3339 instant ending in Z",
        ),
    ] {
        let refused = RecordSearch::new("x", None, Order::Newest, since, before, 5).unwrap_err();
        assert_eq!(refused, why);
    }
}

/// A record matches on its text, a call on its name and its text as
/// `name text` (item 6); counts are non-overlapping, folded a scalar at a
/// time as memory search folds, and the length is the folded scalars.
#[test]
fn a_record_is_counted_on_its_text_and_a_call_on_its_name_too() {
    let terms = ["aa".to_string(), "bash".to_string(), "k".to_string()];
    let reply = record(Kind::Reply, None, "AAAA aaa \u{212a} BASH");
    assert_eq!(record_facts(&reply, &terms), (vec![3, 1, 1], 15));
    let call = record(Kind::ToolCall, Some("Bash"), "{\"cmd\":\"aa\"}");
    assert_eq!(record_facts(&call, &terms), (vec![1, 1, 0], 17));
    let named = record(Kind::ToolCall, Some("KBash"), "{}");
    assert_eq!(record_facts(&named, &terms), (vec![0, 1, 1], 8));
    let sigma = record(Kind::Prompt, None, "ΟΔΟΣ");
    assert_eq!(record_facts(&sigma, &["οδοσ".to_string()]), (vec![1], 4));
}

/// Memory search's rule: up to 240 scalars, starting up to 40 before the
/// first match in the record's own text, its offset in scalars; a call
/// matched on its name alone starts at 0.
#[test]
fn an_excerpt_keeps_its_bound_and_whole_characters() {
    let terms = ["needle".to_string(), "zz".to_string()];
    let text = format!("{}NEEDLE{}", "é中".repeat(500), "😀".repeat(400));
    let (offset, excerpt) = record_excerpt(&text, &terms);
    assert_eq!(offset, 960);
    assert_eq!(excerpt.chars().count(), 240);
    let expected: String = text.chars().skip(960).take(240).collect();
    assert_eq!(excerpt, expected);
    assert!(excerpt.starts_with("é中") && excerpt.contains("NEEDLE"));
    assert_eq!(
        record_excerpt("a needle and zz", &terms),
        (0, "a needle and zz".into())
    );
    let early = format!("{}zz{}", "x".repeat(30), "y".repeat(300));
    let (offset, excerpt) = record_excerpt(&early, &terms);
    assert_eq!((offset, excerpt.len()), (0, 240));
    assert_eq!(
        record_excerpt("{\"cmd\":\"ls\"}", &["bash".to_string()]),
        (0, "{\"cmd\":\"ls\"}".into())
    );
    let (offset, excerpt) = record_excerpt(&format!("{}\u{130}needle", "q".repeat(100)), &terms);
    assert_eq!(offset, 61);
    assert!(excerpt.ends_with("\u{130}needle"));
}
