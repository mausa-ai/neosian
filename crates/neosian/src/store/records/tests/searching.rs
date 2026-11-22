//! History search over turns stored as records (§40.54 item 15.1, T1b2a1),
//! against a twin store holding the same turns whole (same numbers, times
//! and actors): every order answers the same, a turn past the bound keeps
//! its place and score as omitted, a marked seal is rolled forward first,
//! and an omitted turn's size does not reach the search's memory.

use serde_json::json;

use super::super::seal::CRASH_AFTER;
use super::memory::peak;
use super::{enabled, example, messages, part, written};
use crate::conversation::message::Message;
use crate::conversation::page::{Hit, Resume};
use crate::conversation::rank::{Order, Request};
use crate::conversation::search::parse_query;
use crate::conversation::turn_records::records_of;
use crate::pyfmt::dumps;
use crate::store::FileStore;
use crate::testing::Scratch;
use crate::wire::encode;

/// A tool result of `size` bytes with a term on every line.
fn long(size: usize, term: &str) -> Vec<Message> {
    let line = format!("{term} {}\n", "z".repeat(60));
    messages(&[
        json!({"role": "user", "content": "alpha run the long job"}),
        json!({"role": "tool", "content": line.repeat(size / line.len()), "tool_call_id": "t"}),
    ])
}

/// A store with records and its twin holding every turn whole.
fn twins(name: &str) -> (Scratch, FileStore, Scratch, FileStore) {
    let (records_home, records) = enabled(&format!("{name}-records"));
    records
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "alpha tests begin"})]),
            Some("cli:t".into()),
        )
        .unwrap();
    written(&records, "c-1", "d1", &example());
    written(&records, "c-1", "d2", &long(200_000, "zeta"));
    records
        .append_turn(
            "c-1",
            messages(&[json!({"role": "assistant", "content": "alpha done, tests pass"})]),
            None,
        )
        .unwrap();
    written(
        &records,
        "c-2",
        "d3",
        &messages(&[json!({"role": "user", "content": "alpha again, make test"})]),
    );
    let whole_home = Scratch::new(&format!("{name}-whole"));
    let whole = FileStore::open(whole_home.path()).unwrap();
    for id in ["c-1", "c-2"] {
        whole
            .restore_conversation(id, &records.read_turns(id, 0, None).unwrap(), &[], &[])
            .unwrap();
    }
    (records_home, records, whole_home, whole)
}

fn request(query: &str, order: Order) -> Request {
    Request {
        terms: parse_query(query).unwrap(),
        ids: None,
        limit: 50,
        order,
        since: None,
        before: None,
    }
}

/// Every winner as answered, with its score.
fn answer(store: &FileStore, request: &Request, max: Option<u64>) -> Vec<(String, Option<u64>)> {
    let mut found = Vec::new();
    store
        .rank_bounded(
            request,
            &Resume::Fresh,
            &mut |hit, score| {
                found.push((
                    match hit {
                        Hit::Turn(turn) => dumps(&encode::turn(&turn), true),
                        Hit::Omitted(last) => format!(
                            "omitted {} {} {}",
                            last.conversation, last.turn, last.created
                        ),
                    },
                    score,
                ));
                Ok(true)
            },
            max,
        )
        .unwrap();
    found
}

const QUERIES: [&str; 6] = [
    "alpha",
    "tests pass",
    "zeta",
    "make test",
    "a",
    "bash tests",
];

#[test]
fn every_order_answers_records_turns_as_the_same_turns_stored_whole() {
    let (_a, records, _b, whole) = twins("search-exact");
    for query in QUERIES {
        for order in [Order::Newest, Order::Relevance] {
            let got = answer(&records, &request(query, order), Some(1 << 30));
            assert_eq!(
                got,
                answer(&whole, &request(query, order), None),
                "{query} {order:?}"
            );
            assert!(!got.is_empty(), "{query} matches");
        }
        let terms = parse_query(query).unwrap();
        let spelled = |store: &FileStore| -> Vec<String> {
            store
                .search_turns(&terms, None, 50)
                .unwrap()
                .iter()
                .map(|turn| dumps(&encode::turn(turn), true))
                .collect()
        };
        assert_eq!(spelled(&records), spelled(&whole), "version 1: {query}");
        let ranked = |store: &FileStore| {
            store
                .rank_turns(&request(query, Order::Relevance))
                .unwrap()
                .into_iter()
                .map(|(t, s)| (t.turn, s))
                .collect::<Vec<_>>()
        };
        assert_eq!(ranked(&records), ranked(&whole), "rank_turns: {query}");
    }
}

#[test]
fn a_turn_past_the_bound_keeps_its_place_and_score_as_omitted() {
    let (_a, records, _b, whole) = twins("search-omitted");
    // Past 65,536 bytes: item 3.8's turn (c-1 #2, 812,400) and the long job (c-1 #3).
    let past: Vec<(String, String)> = whole
        .read_turns("c-1", 1, Some(2))
        .unwrap()
        .iter()
        .map(|turn| {
            let spelled = dumps(&encode::turn(turn), true);
            (
                spelled,
                format!("omitted c-1 {} {}", turn.turn, turn.created_at.instant()),
            )
        })
        .collect();
    let mut seen = 0;
    for query in QUERIES {
        for order in [Order::Newest, Order::Relevance] {
            let got = answer(&records, &request(query, order), Some(65_536));
            let want = answer(&whole, &request(query, order), None);
            assert_eq!(
                got.len(),
                want.len(),
                "{query} {order:?}: nothing dropped, nothing replaces it"
            );
            for ((hit, score), (turn, expected)) in got.iter().zip(&want) {
                assert_eq!(score, expected, "{query} {order:?}: scores unchanged");
                match past.iter().find(|(spelled, _)| spelled == turn) {
                    Some((_, named)) => {
                        assert_eq!(hit, named, "{query} {order:?}");
                        seen += 1;
                    }
                    None => assert_eq!(hit, turn, "{query} {order:?}"),
                }
            }
        }
    }
    assert!(seen >= 8, "the long turns were winners {seen} times");
    let version_one = records
        .search_turns(&parse_query("zeta").unwrap(), None, 50)
        .unwrap();
    assert_eq!(
        version_one.len(),
        1,
        "within the default bound it is answered whole"
    );
}

#[test]
fn a_marked_seal_is_rolled_forward_before_a_search() {
    let (_home, store) = enabled("search-settle");
    let records = records_of(&messages(&[
        json!({"role": "user", "content": "omega sealed late"}),
    ]))
    .unwrap();
    CRASH_AFTER.with(|crash| crash.set(2));
    assert!(part(&store, "c-3", "d", records, true).is_err());
    CRASH_AFTER.with(|crash| crash.set(0));
    let found = answer(&store, &request("omega", Order::Relevance), None);
    assert_eq!(found.len(), 1);
    assert!(
        !store
            .conversation_dir("c-3")
            .unwrap()
            .join(".drafts/sealing")
            .exists()
    );
}

#[test]
fn an_omitted_turns_size_does_not_reach_the_searchs_memory() {
    let held = |name: &str, size: usize| {
        let (_home, store) = enabled(name);
        written(&store, "c-1", "d", &long(size, "needle"));
        let mut peaks = Vec::new();
        for order in [Order::Newest, Order::Relevance] {
            let (hits, bytes) = peak(|| answer(&store, &request("needle", order), Some(65_536)));
            assert!(hits[0].0.starts_with("omitted"), "{hits:?}");
            peaks.push(bytes);
        }
        peaks
    };
    let small = held("search-memory-small", 2 << 20);
    let large = held("search-memory-large", 16 << 20);
    eprintln!("search peaks: 2 MiB omitted turn {small:?}; 16 MiB {large:?}");
    for (small, large) in small.iter().zip(&large) {
        assert!(
            *large <= small + (128 << 10) && *large < 1 << 20,
            "{small} then {large}"
        );
    }
}

#[test]
fn a_store_without_the_marker_searches_as_before() {
    let home = Scratch::new("search-plain");
    let store = FileStore::open(home.path()).unwrap();
    store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "alpha"})]),
            None,
        )
        .unwrap();
    let header = r#"{"neosian_format":2,"turn":2,"created_at":"2026-11-21T14:03:07Z","actor":null,"records":1,"bytes":5,"draft":"d"}"#;
    let log = store.conversation_dir("c-1").unwrap().join("turns.jsonl");
    let mut text = std::fs::read_to_string(&log).unwrap();
    text.push_str(header);
    text.push('\n');
    std::fs::write(&log, text).unwrap();
    assert_eq!(
        answer(&store, &request("alpha", Order::Newest), None).len(),
        1,
        "a header line without the term is passed over"
    );
    let refused = store
        .rank_bounded(
            &request("neosian_format", Order::Newest),
            &Resume::Fresh,
            &mut |_, _| Ok(true),
            None,
        )
        .unwrap_err();
    assert!(
        refused
            .text()
            .contains("turn-log line 2 format 2 is newer than supported"),
        "{}",
        refused.text()
    );
}
