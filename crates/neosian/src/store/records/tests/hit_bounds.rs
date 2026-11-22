//! A record search on a FileStore, its selection, its twin and its bounds
//! (§40.54 item 11, T1b2a2): `kinds`, time bounds and conversations
//! selecting records, records and the same turns stored whole answering
//! alike in both orders, the candidate bound counted in records, memory
//! following the record (a whole format-1 turn held as its legacy reads
//! hold it), and a store without the marker answering from whole turns as
//! history search does.

use serde_json::{Value, json};

use super::super::seal::CRASH_AFTER;
use super::hits::{mixed, row, search, seen};
use super::memory::peak;
use super::searching::twins;
use super::{enabled, messages, part, written};
use crate::conversation::message::Message;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{Kind, RecordHit, RecordSearch, records_of};
use crate::store::FileStore;
use crate::testing::Scratch;

#[test]
fn kinds_and_time_bounds_select_records() {
    let (_home, store) = mixed("records-hits-select");
    let only = |kinds: Vec<Kind>, query: &str| {
        let request = RecordSearch::new(query, Some(kinds), Order::Newest, None, None, 20).unwrap();
        seen(&store.search_records(&request).unwrap())
    };
    assert_eq!(
        only(vec![Kind::ToolResult], "alpha"),
        [row(
            "c-a",
            1,
            2,
            "tool_result",
            Some("Bash"),
            "alpha found twice: alpha"
        )]
    );
    assert_eq!(
        only(vec![Kind::ToolCall, Kind::Reply], "bash"),
        [row(
            "c-a",
            1,
            1,
            "tool_call",
            Some("Bash"),
            "{\"cmd\":\"grep alpha\"}"
        )]
    );
    assert_eq!(
        only(vec![Kind::Reply], "done"),
        [row("c-a", 1, 3, "reply", None, "done")]
    );
    assert!(only(Vec::new(), "alpha").is_empty());
    let at = |text: &str| Some(text.to_string());
    let window = |since, before| {
        let request = RecordSearch::new("alpha", None, Order::Newest, since, before, 20).unwrap();
        let hits = store.search_records(&request).unwrap();
        hits.iter()
            .map(|h| (h.conversation_id.clone(), h.turn))
            .collect::<Vec<_>>()
    };
    let early = window(None, at("2026-11-21T10:00:01Z"));
    assert_eq!(
        early,
        [
            ("c-b".into(), 1),
            ("c-a".into(), 1),
            ("c-a".into(), 1),
            ("c-a".into(), 1)
        ]
    );
    assert_eq!(
        window(at("2026-11-21T10:00:01Z"), None),
        [("c-a".to_string(), 2)]
    );
    let mut given = search("alpha", Order::Newest);
    given.conversations = Some(vec!["c-b".into(), "c-b".into()]);
    assert_eq!(
        seen(&store.search_records(&given).unwrap()),
        [row("c-b", 1, 0, "prompt", None, "alpha")]
    );
    given.conversations = Some(Vec::new());
    assert!(store.search_records(&given).unwrap().is_empty());
    given.conversations = Some(vec!["../x".into()]);
    assert!(store.search_records(&given).is_err());
}

#[test]
fn records_and_whole_turns_answer_alike() {
    let (_rh, records, _wh, whole) = twins("records-hits-twins");
    for query in [
        "alpha",
        "tests",
        "zeta",
        "make test",
        "bash",
        "\"command\":\"make",
        "z",
    ] {
        for order in [Order::Newest, Order::Relevance] {
            let mut request = search(query, order);
            request.limit = 100;
            let answer = |store: &FileStore| -> Vec<Value> {
                store
                    .search_records(&request)
                    .unwrap()
                    .iter()
                    .map(RecordHit::to_json)
                    .collect()
            };
            let (left, right) = (answer(&records), answer(&whole));
            assert!(!left.is_empty() || query == "\"command\":\"make", "{query}");
            assert_eq!(left, right, "{query} {order:?}");
        }
    }
}

#[test]
fn the_candidate_bound_counts_records() {
    let (_home, store) = enabled("records-hits-bound");
    let result = format!("{}\n", "beta ".repeat(13_000));
    let turn = messages(&[
        json!({"role": "user", "content": "run"}),
        json!({"role": "tool", "content": result.repeat(4), "tool_call_id": "t"}),
    ]);
    let records = records_of(&turn).unwrap();
    let holding = records.iter().filter(|r| r.text.contains("beta")).count();
    assert!(holding >= 4, "{holding} records hold the term");
    written(&store, "c-1", "d", &turn);
    let request = search("beta", Order::Relevance);
    let refused = store
        .search_records_bounded(&request, holding - 1)
        .unwrap_err()
        .text();
    assert_eq!(
        refused,
        format!(
            "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than {} records hold a search term; narrow the words, the kinds or the time range",
            holding - 1
        )
    );
    let hits = store.search_records_bounded(&request, holding).unwrap();
    assert_eq!(hits.len(), holding);
}

/// One `size`-byte tool result holding the term on every line, stored as
/// records (`records`) or whole; the most a record search over it held at
/// once, and for a whole turn the most history search held.
fn search_peak(name: &str, size: usize, records: bool, order: Order) -> (usize, usize) {
    let (_home, store) = enabled(name);
    let line = format!("omega {}\n", "w".repeat(90));
    let turn = |text: String| -> Vec<Message> {
        messages(&[
            json!({"role": "user", "content": "run it"}),
            json!({"role": "tool", "content": text, "tool_call_id": "t"}),
        ])
    };
    let text = line.repeat(size / line.len());
    if records {
        written(&store, "c-1", "d", &turn(text));
    } else {
        store.append_turn("c-1", turn(text), None).unwrap();
    }
    let request = search("omega", order);
    let (hits, held) = peak(|| store.search_records(&request).unwrap());
    assert!(hits.len() >= 16, "{} hits", hits.len());
    let mut history = 0;
    if !records {
        let legacy;
        (_, legacy) = peak(|| store.read_turns("c-1", 0, None).unwrap());
        (_, history) = peak(|| {
            let request = crate::conversation::rank::Request {
                terms: vec!["omega".into()],
                ids: None,
                limit: 20,
                order,
                since: None,
                before: None,
            };
            store.rank_turns(&request).unwrap()
        });
        eprintln!("{order:?}: whole turn of {size}: read_turns {legacy}, history search {history}");
    }
    (held, history)
}

#[test]
fn memory_during_a_search_follows_the_record() {
    for order in [Order::Newest, Order::Relevance] {
        let (small, _) = search_peak("records-hits-memory-small", 1 << 20, true, order);
        let (large, _) = search_peak("records-hits-memory-large", 16 << 20, true, order);
        eprintln!("{order:?}: records turn of 1 MiB peaked at {small} bytes, 16 MiB at {large}");
        assert!(large < 1 << 20, "{large}");
        assert!(large <= small + (64 << 10), "{small} then {large}");
        let (whole, history) = search_peak("records-hits-memory-whole", 16 << 20, false, order);
        eprintln!("{order:?}: the same 16 MiB stored whole peaked at {whole} bytes");
        assert!(
            whole > 16 << 20,
            "a whole format-1 turn is held, as its legacy read holds it"
        );
        assert!(
            whole <= history,
            "no more than history search holds for it: {history}"
        );
    }
}

#[test]
fn a_store_without_the_marker_answers_from_whole_turns() {
    let home = Scratch::new("records-hits-plain");
    let store = FileStore::open(home.path()).unwrap();
    store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "alpha plain"})]),
            None,
        )
        .unwrap();
    let hits = store
        .search_records(&search("alpha", Order::Newest))
        .unwrap();
    assert_eq!(
        seen(&hits),
        [row("c-1", 1, 0, "prompt", None, "alpha plain")]
    );
    let header = r#"{"neosian_format":2,"turn":2,"created_at":"2026-11-21T14:03:07.512000Z","actor":null,"records":1,"bytes":1,"draft":"d"}"#;
    let log = store.conversation_dir("c-1").unwrap().join("turns.jsonl");
    let mut text = std::fs::read_to_string(&log).unwrap();
    text.push_str(header);
    text.push('\n');
    std::fs::write(&log, text).unwrap();
    // As history search on such a store: a row lacking every term is passed
    // over by the raw-line prefilter, one holding a term is refused.
    let hits = store
        .search_records(&search("alpha", Order::Newest))
        .unwrap();
    assert_eq!(
        seen(&hits),
        [row("c-1", 1, 0, "prompt", None, "alpha plain")]
    );
    let refused = store
        .search_records(&search("draft", Order::Newest))
        .unwrap_err()
        .text();
    assert!(
        refused.contains("turn-log line 2 format 2 is newer than supported"),
        "{refused}"
    );
}

#[test]
fn a_marked_seal_is_rolled_forward_before_a_record_search() {
    let (_home, store) = enabled("records-hits-settle");
    let records = records_of(&messages(&[
        json!({"role": "user", "content": "sealed omega"}),
    ]))
    .unwrap();
    CRASH_AFTER.with(|crash| crash.set(2));
    assert!(part(&store, "c-1", "d", records, true).is_err());
    CRASH_AFTER.with(|crash| crash.set(0));
    let hits = store
        .search_records(&search("omega", Order::Newest))
        .unwrap();
    assert_eq!(
        seen(&hits),
        [row("c-1", 1, 0, "prompt", None, "sealed omega")]
    );
}
