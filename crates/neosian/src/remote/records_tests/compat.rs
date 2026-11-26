//! What a store that keeps records changes for the legacy calls (DESIGN
//! §40.54 item 15.1): the refusal of a long record turn is reported once
//! and never asked again in size pages, `omitted` in a history search is
//! accepted wherever the answer is decoded, and the legacy requests keep
//! their bytes whatever the handshake declares.

use serde_json::{Value, json};

use super::declaring;
use crate::conversation::Error;
use crate::conversation::rank::{Order, Request};
use crate::remote::RemoteStore;
use crate::remote::tests::{Stub, capabilities};
use crate::state::State;

/// The refusal of item 15.1 (O11), as a server writes it.
const LONG_TURN: &str = "resource limit NEOSIAN_MAX_TURN_BYTES exceeded: turn 3 of c holds 70000000 > 67108864 bytes; a newer neosian reads this turn in parts";
const TEMP_BUDGET: &str = "resource limit NEOSIAN_MAX_TEMP_BYTES exceeded: 9 > 8; select a smaller batch or raise this setting";

fn refusal(message: &str) -> (u16, Value) {
    let error = json!({"code":"value_error","message":message,"details":{}});
    (400, json!({ "error": error }))
}

fn paging(mut capabilities: Value) -> Value {
    capabilities["size_paging"] = json!({"version": 1});
    capabilities
}

fn turn(number: u64) -> Value {
    let message =
        json!({"role":"user","content":"x","reasoning":null,"tool_calls":[],"tool_call_id":null});
    json!({"conversation_id":"c","turn":number,"messages":[message],"created_at":"2026-01-01T00:00:00Z","actor":null,"redacted":false})
}

fn connect(answers: Vec<(u16, Value)>) -> (Stub, RemoteStore) {
    let server = Stub::replies(answers);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    (server, store)
}

#[test]
fn a_long_record_turn_is_refused_once_with_and_without_size_paging() {
    for handshake in [
        capabilities(),
        paging(capabilities()),
        paging(declaring(json!(1))),
    ] {
        // Two answers and no more: a size-paged retry would find no
        // listener and report a transport fault instead of the refusal.
        let (server, store) = connect(vec![(200, handshake.clone()), refusal(LONG_TURN)]);
        let Err(Error::Value(text)) = store.read_turns("c", 0, None) else {
            panic!("the refusal itself")
        };
        assert_eq!(text, LONG_TURN);
        assert_eq!(server.requests.lock().unwrap().len(), 2);
        drop(server);
        let (server, store) = connect(vec![(200, handshake), refusal(LONG_TURN)]);
        let mut visit = |_| Ok(true);
        let Err(Error::Value(text)) = store.read_turns_each("c", 2, Some(1), &mut visit) else {
            panic!("the refusal itself")
        };
        assert_eq!(text, LONG_TURN);
        assert_eq!(server.requests.lock().unwrap().len(), 2);
    }
}

#[test]
fn omitted_is_accepted_on_unpaged_and_paged_history_searches() {
    let ranked = Request {
        terms: vec!["x".into()],
        ids: None,
        limit: 5,
        order: Order::Relevance,
        since: None,
        before: None,
    };
    let scored = |number: u64, next: Value| json!({"turns":[turn(number)],"scores":[9 - number],"order":"relevance","continuation":next,"omitted":2});
    let mut handshake = paging(declaring(json!(1)));
    handshake["history_ranking"] = json!({"version": 1});
    let (server, store) = connect(vec![
        (200, handshake),
        (200, json!({"turns":[turn(1)],"omitted":2})),
        refusal(TEMP_BUDGET),
        (
            200,
            json!({"turns":[turn(1)],"continuation":"next","omitted":1}),
        ),
        (
            200,
            json!({"turns":[turn(2)],"continuation":null,"omitted":1}),
        ),
        (
            200,
            json!({"turns":[turn(1)],"scores":[7],"order":"relevance","omitted":3}),
        ),
        refusal(TEMP_BUDGET),
        (200, scored(1, json!("next"))),
        (200, scored(2, Value::Null)),
    ]);
    let numbers = |turns: Vec<crate::conversation::Turn>| -> Vec<u64> {
        turns.iter().map(|turn| turn.turn).collect()
    };
    assert_eq!(
        numbers(store.search_turns(&["x".into()], None, 5).unwrap()),
        [1]
    );
    assert_eq!(
        numbers(store.search_turns(&["x".into()], None, 5).unwrap()),
        [1, 2]
    );
    let found = store.rank_turns_wire(&ranked).unwrap();
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].1, Some(7));
    let found = store.rank_turns_wire(&ranked).unwrap();
    let scores: Vec<_> = found
        .iter()
        .map(|(turn, score)| (turn.turn, *score))
        .collect();
    assert_eq!(scores, [(1, Some(8)), (2, Some(7))]);
    assert_eq!(server.requests.lock().unwrap().len(), 9);
}

#[test]
fn the_legacy_requests_keep_their_bytes_beside_turn_records() {
    for handshake in [capabilities(), paging(declaring(json!(1)))] {
        let (server, store) = connect(vec![
            (200, handshake.clone()),
            (200, json!({"turns":[turn(1)],"next_after":null})),
            (200, json!({"turns":[turn(1)]})),
            (200, json!({"turn":4})),
        ]);
        store.read_turns("c", 0, None).unwrap();
        store.search_turns(&["x".into()], None, 5).unwrap();
        assert_eq!(store.last_turn_number("c").unwrap(), 4);
        let raw = server.raw.lock().unwrap();
        assert_eq!(raw[0], "");
        // The legacy path sends its JSON indented by two spaces, as every
        // release since 2.16.0 has.
        assert_eq!(
            raw[1],
            "{\n  \"conversation_id\": \"c\",\n  \"after\": 0,\n  \"limit\": null\n}"
        );
        assert_eq!(
            raw[2],
            "{\n  \"query\": \"x\",\n  \"conversations\": null,\n  \"limit\": 5\n}"
        );
        assert_eq!(raw[3], "{\n  \"conversation_id\": \"c\"\n}");
    }
    // The size-paged retry of 2.17.0, asked only after a size refusal.
    let (server, store) = connect(vec![
        (200, paging(declaring(json!(1)))),
        refusal(TEMP_BUDGET),
        (
            200,
            json!({"turns":[turn(1)],"next_after":null,"continuation":null}),
        ),
    ]);
    store.read_turns("c", 0, None).unwrap();
    let raw = server.raw.lock().unwrap();
    assert_eq!(
        raw[2],
        "{\n  \"conversation_id\": \"c\",\n  \"after\": 0,\n  \"limit\": null,\n  \"page_bytes\": 983040\n}"
    );
}
