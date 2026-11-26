//! Loopback exchanges for the turn-records client: the capability gate,
//! the typed failure, paging and the absence of any retry. The request and
//! answer shapes are checked against wire.md's own examples in
//! `examples.rs` and `shapes.rs`, the legacy paths in `compat.rs`, the
//! checks on a search answer in `search.rs`, a read that goes backwards in
//! `forward.rs`, refusals with a broken or foreign body in `refusals.rs`,
//! and a real server in `live.rs`.

#[path = "records_tests/compat.rs"]
mod compat;
#[path = "records_tests/examples.rs"]
mod examples;
#[path = "records_tests/forward.rs"]
mod forward;
#[path = "records_tests/live.rs"]
mod live;
#[path = "records_tests/refusals.rs"]
mod refusals;
#[path = "records_tests/search.rs"]
mod search;
#[path = "records_tests/shapes.rs"]
mod shapes;

use serde_json::{Value, json};

use super::*;
use crate::conversation::message::Role;
use crate::conversation::turn_records::{Form, State};
use crate::remote::tests::{Stub, capabilities};

/// A handshake that declares turn records at `version`.
fn declaring(version: Value) -> Value {
    let mut capabilities = capabilities();
    capabilities["turn_records"] = json!({ "version": version });
    capabilities
}

/// A store connected to a stub that declares version 1 and then answers
/// `answers` in order.
fn connected(answers: Vec<(u16, Value)>) -> (Stub, RemoteStore) {
    let mut all = vec![(200, declaring(json!(1)))];
    all.extend(answers);
    let server = Stub::replies(all);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    (server, store)
}

/// One call against a stub that answers `answer`: the request body as it
/// was sent, and what the call returned.
fn asked<T>(answer: &str, call: impl FnOnce(&RemoteStore) -> T) -> (String, T) {
    let (server, store) = connected(vec![(200, json!({ "$raw": answer }))]);
    let returned = call(&store);
    let sent = server.raw.lock().unwrap()[1].clone();
    (sent, returned)
}

/// What an unreadable answer names.
fn unreadable<T>(result: Result<T, Failure>) -> String {
    match result {
        Err(Failure::Unreadable(what)) => what,
        Err(other) => panic!("an unreadable answer, not {other}"),
        Ok(_) => panic!("an unreadable answer, not an accepted one"),
    }
}

fn record(seq: u64, message: u64, role: Role, kind: Kind, text: &str) -> TurnRecord {
    TurnRecord {
        seq,
        message,
        role,
        kind,
        content: Some(Form::Text),
        block: None,
        call: None,
        call_id: None,
        name: None,
        chunk: 0,
        size: text.len() as u64,
        text: text.to_string(),
    }
}

fn part(draft: &str) -> Write {
    Write {
        draft: draft.into(),
        records: vec![record(0, 0, Role::User, Kind::Prompt, "hello")],
        ..Write::default()
    }
}

/// The refusal a failure carries.
fn refused(failure: Failure) -> Refusal {
    match failure {
        Failure::Refused(refusal) => *refusal,
        other => panic!("a refusal, not {other}"),
    }
}

fn envelope(code: &str, message: &str, details: Value) -> Value {
    json!({"error":{"code":code,"message":message,"details":details}})
}

/// Each of the five methods once, in a fixed order.
fn each_method(store: &RemoteStore) -> Vec<Failure> {
    let restore = Restore {
        restore: "r1".into(),
        ..Restore::default()
    };
    let query = RecordQuery {
        query: "x".into(),
        ..RecordQuery::default()
    };
    let read = RecordRead::default();
    vec![
        store.write_records("c", &part("d1")).unwrap_err(),
        store
            .read_records("c", &read, &mut |_| Ok(true))
            .unwrap_err(),
        store.search_records(&query).unwrap_err(),
        store.restore_records("c", &restore).unwrap_err(),
        store.sweep_records(50, None, false).unwrap_err(),
    ]
}

#[test]
fn support_is_version_1_exactly_and_nothing_is_sent_without_it() {
    let (_server, store) = connected(vec![]);
    assert!(store.supports_records());
    for handshake in [
        capabilities(),
        declaring(json!(2)),
        declaring(json!("1")),
        declaring(json!(1.5)),
        declaring(Value::Null),
    ] {
        // The stub answers the handshake alone: a second request would
        // find no listener and fail as a transport fault.
        let server = Stub::replies(vec![(200, handshake)]);
        let store = RemoteStore::connect(&server.url, "test-token").unwrap();
        assert!(!store.supports_records());
        for failure in each_method(&store) {
            assert!(matches!(failure, Failure::Unsupported), "{failure}");
        }
        assert_eq!(server.requests.lock().unwrap().len(), 1);
    }
}

#[test]
fn each_conflict_keeps_its_reason_and_details_as_sent() {
    let header = json!({"turn":42,"created_at":"2026-11-21T14:03:07.512000Z","actor":"client:default/a","redacted":false,"records":16,"bytes":812400});
    let position = json!({"turn":1,"seq":3,"complete":true,"projections":0,"redactions":0});
    let draft: Vec<(&str, Value)> = vec![
        ("draft_gap", json!({"next_seq":9})),
        ("draft_mismatch", json!({"next_seq":9,"seq":4})),
        ("draft_actor", json!({"draft":"d1"})),
        ("draft_size", json!({"turn_bytes":812400,"bytes":812401})),
        ("draft_sealed", json!({ "turn": header })),
        ("drafts_open", json!({"limit":16})),
    ];
    let restore: Vec<(&str, Value)> = vec![
        ("target_occupied", json!({})),
        ("restore_open", json!({"restore":"r0"})),
        ("restore_gap", json!({ "position": position })),
        ("restore_mismatch", json!({ "position": position })),
        ("restore_incomplete", json!({ "position": position })),
        ("restore_committed", json!({ "position": position })),
    ];
    let details = |(reason, extra): &(&str, Value)| {
        let mut all = json!({"conversation_id":"c","reason":reason});
        let extra = extra.as_object().unwrap().clone();
        all.as_object_mut().unwrap().extend(extra);
        all
    };
    let refusal = |case| {
        (
            400,
            envelope("agent_conversation_conflict", "no", details(case)),
        )
    };
    let answers = draft.iter().chain(&restore).map(refusal).collect();
    let (server, store) = connected(answers);
    let probe = Restore {
        restore: "r1".into(),
        ..Restore::default()
    };
    for (at, case) in draft.iter().chain(&restore).enumerate() {
        let failure = match at < draft.len() {
            true => store.write_records("c", &part("d1")).unwrap_err(),
            false => store.restore_records("c", &probe).unwrap_err(),
        };
        assert_eq!(failure.reason(), Some(case.0));
        let kept = refused(failure);
        assert_eq!((kept.status, kept.retry_after), (400, None));
        assert_eq!(kept.code.as_deref(), Some("agent_conversation_conflict"));
        let kept = kept.details;
        // Verbatim: the same keys in the same order, nested values whole.
        assert_eq!(kept.to_string(), details(case).to_string());
    }
    // One request for each refusal: none was asked again.
    assert_eq!(server.requests.lock().unwrap().len(), 13);
}

const BODY_TOO_LONG: &str = "request body exceeds 4194304 bytes";
const TURN_TOO_LARGE: &str = "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 9 > 8; this store does not keep a turn this large";

#[test]
fn a_full_destination_is_told_from_a_size_refusal() {
    let (server, store) = connected(vec![
        (
            413,
            json!({"error":{"code":"storage_exceeded","message":"the account is full","details":{"limit":5},"v1_family":null}}),
        ),
        (413, envelope("value_error", BODY_TOO_LONG, json!({}))),
        (413, envelope("value_error", TURN_TOO_LARGE, json!({}))),
        (403, envelope("forbidden", "outside allowance", json!({}))),
    ]);
    let mut seen = Vec::new();
    for _ in 0..4 {
        let kept = refused(store.write_records("c", &part("d1")).unwrap_err());
        seen.push((kept.status, kept.code.unwrap(), kept.message, kept.details));
    }
    let full = (413, "storage_exceeded".to_string());
    assert_eq!((seen[0].0, seen[0].1.clone()), full);
    assert_eq!(seen[0].2, "the account is full");
    assert_eq!(seen[0].3, json!({"limit":5}));
    // The two size refusals share a status and a code, apart from the full
    // destination's, and differ from their first word.
    for (at, message) in [(1, BODY_TOO_LONG), (2, TURN_TOO_LARGE)] {
        assert_eq!((seen[at].0, seen[at].1.as_str()), (413, "value_error"));
        assert_eq!(seen[at].2, message);
    }
    assert_eq!((seen[3].0, seen[3].1.as_str()), (403, "forbidden"));
    assert_eq!(server.requests.lock().unwrap().len(), 5);
}

#[test]
fn retry_after_survives_with_and_without_an_envelope() {
    let limited = envelope("rate_limited", "slow down", json!({})).to_string();
    let (server, store) = connected(vec![
        (429, json!({"$raw": limited, "$header": "Retry-After: 7"})),
        (
            429,
            json!({"$raw": "<html>Too Many Requests</html>", "$header": "retry-after:  120 "}),
        ),
        (
            429,
            json!({"$raw": "", "$header": "Retry-After: Wed, 21 Oct 2026 07:28:00 GMT"}),
        ),
        (429, json!({"$raw": "busy"})),
        (
            503,
            json!({"$raw": "{\"error\":\"down\"}", "$header": "Retry-After: -5"}),
        ),
    ]);
    let mut seen = Vec::new();
    for _ in 0..5 {
        let kept = refused(store.sweep_records(50, None, false).unwrap_err());
        seen.push((
            kept.status,
            kept.retry_after,
            kept.code,
            kept.message,
            kept.details,
        ));
    }
    // Without an envelope the body's start is the message.
    let bare = |status, seconds, body: &str| (status, seconds, None, body.to_string(), Value::Null);
    assert_eq!(
        seen[0],
        (
            429,
            Some(7),
            Some("rate_limited".into()),
            "slow down".into(),
            json!({})
        )
    );
    assert_eq!(
        seen[1],
        bare(429, Some(120), "<html>Too Many Requests</html>")
    );
    // An HTTP date, a missing header and a malformed one read as absent.
    assert_eq!(seen[2], bare(429, None, ""));
    assert_eq!(seen[3], bare(429, None, "busy"));
    assert_eq!(seen[4], bare(503, None, "{\"error\":\"down\"}"));
    assert_eq!(server.requests.lock().unwrap().len(), 6);
}

#[test]
fn a_transport_fault_an_unreadable_answer_and_a_refusal_differ() {
    let written = json!({"draft":"d1","state":"open","next_seq":1,"turn":null,"stored_bytes":1,"freed_bytes":0});
    let mut mistyped = written.clone();
    mistyped["next_seq"] = json!("1");
    let mut unknown = written.clone();
    unknown["state"] = json!("pending");
    let (server, store) = connected(vec![
        (0, Value::Null),
        (500, json!("not an envelope")),
        (302, json!({})),
        (200, json!({"$raw": "{\"draft\":\"d1\",\"sta"})),
        (200, json!([])),
        (200, json!({})),
        (200, mistyped),
        (200, unknown),
        (200, written),
    ]);
    let write = || store.write_records("c", &part("d1"));
    assert!(matches!(write(), Err(Failure::Transport(_))));
    for status in [500, 302] {
        let kept = refused(write().unwrap_err());
        assert_eq!((kept.status, kept.code), (status, None));
    }
    for _ in 0..5 {
        assert!(matches!(write(), Err(Failure::Unreadable(_))));
    }
    assert_eq!(write().unwrap().state, State::Open);
    // Ten requests for nine calls and the handshake: nothing was retried,
    // the lost answer included.
    assert_eq!(server.requests.lock().unwrap().len(), 10);
}

fn page(turn: u64, seq: u64, continuation: Option<&str>) -> Value {
    json!({
        "turns":[{"turn":turn,"created_at":"2026-11-21T14:03:07Z","actor":null,"redacted":false,"records":3,"bytes":3}],
        "records":[{"turn":turn,"seq":seq,"message":0,"role":"user","kind":"prompt","content":null,"block":null,"call":null,
                    "call_id":null,"name":null,"chunk":seq,"size":3,"text":"x","tail":null,"truncated":false}],
        "continuation":continuation,
    })
}

#[test]
fn a_read_follows_continuations_page_by_page_to_the_end() {
    let (server, store) = connected(vec![
        (200, page(1, 0, Some("one"))),
        (200, page(1, 1, Some("two"))),
        (200, page(1, 2, None)),
        (200, page(1, 1, Some("two"))),
        (200, page(1, 2, Some("three"))),
    ]);
    let read = RecordRead {
        turn: Some(1),
        ..RecordRead::default()
    };
    let mut pages = Vec::new();
    let mut visit = |page: RecordPage| {
        assert_eq!((page.turns.len(), page.records.len()), (1, 1));
        pages.push((page.records[0].record.seq, page.continuation));
        Ok(true)
    };
    store.read_records("c", &read, &mut visit).unwrap();
    let all = [(0, Some("one")), (1, Some("two")), (2, None)];
    assert_eq!(pages, all.map(|(seq, next)| (seq, next.map(String::from))));
    // A caller that kept a continuation resumes there, and one that says
    // stop is asked nothing more though the page names a next one.
    let resumed = RecordRead {
        continuation: Some("one".into()),
        ..read.clone()
    };
    let mut seen = 0;
    let mut once = |_: RecordPage| {
        seen += 1;
        Ok(false)
    };
    store.read_records("c", &resumed, &mut once).unwrap();
    // The visitor's own failure ends the read as itself.
    let mut fail = |_: RecordPage| Err(Failure::Unreadable("mine".into()));
    let ended = store.read_records("c", &read, &mut fail).unwrap_err();
    assert!(matches!(ended, Failure::Unreadable(what) if what == "mine"));
    assert_eq!(seen, 1);
    let raw = server.raw.lock().unwrap();
    assert_eq!(raw.len(), 6);
    assert_eq!(raw[1], r#"{"conversation_id":"c","turn":1}"#);
    assert_eq!(
        raw[2],
        r#"{"conversation_id":"c","turn":1,"continuation":"one"}"#
    );
    assert_eq!(
        raw[3],
        r#"{"conversation_id":"c","turn":1,"continuation":"two"}"#
    );
    assert_eq!(raw[4], raw[2]);
    assert_eq!(raw[5], raw[1]);
}

#[test]
fn a_continuation_that_cannot_end_the_read_is_unreadable() {
    let empty = json!({"turns":[],"records":[],"continuation":"next"});
    let long = "x".repeat(4097);
    let (server, store) = connected(vec![
        (200, empty),
        (200, page(1, 0, Some(""))),
        (200, page(1, 0, Some(&long))),
        (200, page(1, 0, Some("same"))),
        (200, page(1, 1, Some("same"))),
        (200, json!({"turns":[],"records":[],"continuation":null})),
    ]);
    let read = RecordRead::default();
    let mut visited = 0;
    let mut visit = |_: RecordPage| {
        visited += 1;
        Ok(true)
    };
    for _ in 0..4 {
        let ended = store.read_records("c", &read, &mut visit).unwrap_err();
        assert!(matches!(ended, Failure::Unreadable(what) if what == "continuation"));
    }
    // An empty last page is a whole answer: a conversation with no turn.
    store.read_records("c", &read, &mut visit).unwrap();
    assert_eq!(visited, 2);
    assert_eq!(server.requests.lock().unwrap().len(), 7);
}
