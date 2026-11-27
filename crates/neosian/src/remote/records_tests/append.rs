//! The lander's calls beside the records routes (DESIGN §40.54 items 15.1,
//! 15.2 and 17.4; rulings D7 and D8 of the T1e1 plan): the fallback
//! `append_turn` sends the legacy call's bytes and keeps what a refusal
//! carries; the first turn's time comes from its header while `read_turns`
//! of that turn is refused; and the lander's sink takes records only from
//! a server that declares them.

use serde_json::{Value, json};

use super::declaring;
use crate::conversation::Error;
use crate::conversation::message::{Message, Role, ToolCall, to_json};
use crate::conversation::turn_records::Header;
use crate::record::landing::sink::Sink;
use crate::remote::records::Failure;
use crate::remote::tests::{Stub, capabilities};
use crate::remote::{RemoteStore, codec};
use crate::state::State;
use crate::store::time::Stamp;

const ACTOR: &str = "claude-code:7c1e2b3a";
/// The refusal of item 15.1 (O11) for a long first turn.
const LONG_TURN: &str = "resource limit NEOSIAN_MAX_TURN_BYTES exceeded: turn 1 of c holds 70000000 > 67108864 bytes; a newer neosian reads this turn in parts";

/// A turn with what JSON escapes and what it leaves raw: quotes, a
/// backslash, control characters, multi-byte text, a tool round.
fn messages() -> Vec<Message> {
    let mut call = Message::text(Role::Assistant, String::new());
    call.content = None;
    call.tool_calls = vec![ToolCall {
        id: "call-1".into(),
        name: "Bash".into(),
        arguments: json!({"command": "make \"all\""}),
        extra: Value::Null,
    }];
    let mut result = Message::text(Role::Tool, "built é 中 😀\t\u{1}".into());
    result.tool_call_id = Some("call-1".into());
    vec![
        Message::text(Role::User, "say \"hi\" \\ then\nstop".into()),
        call,
        result,
        Message::text(Role::Assistant, "done".into()),
    ]
}

fn stored() -> Value {
    let messages: Vec<Value> = messages().iter().map(to_json).collect();
    json!({"turn": {"conversation_id": "c", "turn": 4, "messages": messages,
        "created_at": "2026-11-26T12:00:00Z", "actor": format!("client:test/{ACTOR}"),
        "redacted": false}})
}

fn connect(answers: Vec<(u16, Value)>) -> (Stub, RemoteStore) {
    let server = Stub::replies(answers);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    (server, store)
}

/// The body of `append_turn` for `messages()`, as every release since
/// 2.16.0 writes it: indented by two spaces, non-ASCII raw.
const LEGACY_BODY: &str = "{\n  \"conversation_id\": \"c\",\n  \"messages\": [\n    {\n      \"role\": \"user\",\n      \"content\": \"say \\\"hi\\\" \\\\ then\\nstop\",\n      \"reasoning\": null,\n      \"tool_calls\": [],\n      \"tool_call_id\": null\n    },\n    {\n      \"role\": \"assistant\",\n      \"content\": null,\n      \"reasoning\": null,\n      \"tool_calls\": [\n        {\n          \"id\": \"call-1\",\n          \"name\": \"Bash\",\n          \"arguments\": {\n            \"command\": \"make \\\"all\\\"\"\n          }\n        }\n      ],\n      \"tool_call_id\": null\n    },\n    {\n      \"role\": \"tool\",\n      \"content\": \"built é 中 😀\\t\\u0001\",\n      \"reasoning\": null,\n      \"tool_calls\": [],\n      \"tool_call_id\": \"call-1\"\n    },\n    {\n      \"role\": \"assistant\",\n      \"content\": \"done\",\n      \"reasoning\": null,\n      \"tool_calls\": [],\n      \"tool_call_id\": null\n    }\n  ],\n  \"actor\": \"claude-code:7c1e2b3a\"\n}";

#[test]
fn the_fallback_sends_the_legacy_append_turn_bytes() {
    for handshake in [capabilities(), declaring(json!(1))] {
        let (legacy, store) = connect(vec![(200, handshake.clone()), (200, stored())]);
        let was = State::append_turn(&store, "c", messages(), Some(ACTOR.into())).unwrap();
        drop(store);
        let (kept, store) = connect(vec![(200, handshake), (200, stored())]);
        let now = store
            .append_turn_kept("c", &messages(), Some(ACTOR))
            .unwrap();
        let (legacy, kept) = (legacy.raw.lock().unwrap(), kept.raw.lock().unwrap());
        assert_eq!(kept[1], legacy[1]);
        assert_eq!(kept[1], LEGACY_BODY);
        assert_eq!(kept.len(), 2);
        assert_eq!(
            (now.turn, now.actor, now.created_at),
            (was.turn, was.actor, was.created_at)
        );
        let spelled = |m: &[Message]| m.iter().map(to_json).collect::<Vec<_>>();
        assert_eq!(spelled(&now.messages), spelled(&was.messages));
    }
}

#[test]
fn the_fallback_keeps_what_a_refusal_carries() {
    let envelope = |code: &str| json!({"error": {"code": code, "message": "m", "details": {}}});
    let mut throttled = envelope("rate_limited");
    throttled["$header"] = json!("Retry-After: 7");
    let (server, store) = connect(vec![
        (200, capabilities()),
        (429, throttled),
        (413, envelope("value_error")),
        (503, json!({"$raw": "<html>down</html>"})),
        (200, json!({"turn": 3})),
        (0, Value::Null),
    ]);
    let refused = |result: Result<_, Failure>| match result {
        Err(Failure::Refused(refusal)) => {
            (refusal.status, refusal.retry_after, refusal.code.clone())
        }
        other => panic!("a refusal, not {:?}", other.err()),
    };
    let send = || store.append_turn_kept("c", &messages(), Some(ACTOR));
    assert_eq!(refused(send()), (429, Some(7), Some("rate_limited".into())));
    assert_eq!(refused(send()), (413, None, Some("value_error".into())));
    assert_eq!(refused(send()), (503, None, None));
    assert!(matches!(send(), Err(Failure::Unreadable(what)) if what == "turn"));
    assert!(matches!(send(), Err(Failure::Transport(_))));
    assert_eq!(server.requests.lock().unwrap().len(), 6);
}

fn header() -> Header {
    Header {
        turn: 1,
        created_at: Stamp::parse("2026-11-20T08:00:00.25Z").unwrap(),
        actor: Some(format!("client:test/{ACTOR}")),
        redacted: false,
        records: 4,
        bytes: 70_000_000,
    }
}

fn page() -> Value {
    json!({"turns": [header().to_json()], "records": [], "continuation": "page-2"})
}

#[test]
fn the_first_turn_s_time_comes_from_its_header_while_read_turns_is_refused() {
    let refusal = json!({"error": {"code": "value_error", "message": LONG_TURN, "details": {}}});
    // A whole read of turn 1 is refused (item 15.1)...
    let (_server, store) = connect(vec![(200, declaring(json!(1))), (400, refusal)]);
    let Err(Error::Value(text)) = State::first_turn_stamp(&store, "c") else {
        panic!("the refusal")
    };
    assert_eq!(text, LONG_TURN);
    // ... while its header answers the time, one header in one page.
    let (server, store) = connect(vec![(200, declaring(json!(1))), (200, page())]);
    let sink = Sink::Remote(Box::new(store));
    assert_eq!(
        sink.first_turn_stamp("c").unwrap(),
        Some(header().created_at)
    );
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[1].0, "POST /v1/conversation/read_records HTTP/1.1");
    let raw = server.raw.lock().unwrap();
    assert_eq!(
        raw[1],
        r#"{"conversation_id":"c","kinds":[],"page_bytes":1}"#
    );
}

#[test]
fn a_conversation_without_turns_has_no_first_time() {
    let empty = json!({"turns": [], "records": [], "continuation": null});
    let (_server, store) = connect(vec![(200, declaring(json!(1))), (200, empty)]);
    assert_eq!(store.first_turn_time("c").unwrap(), None);
    // Without turn records the sink reads the first turn as before.
    let none = json!({"turns": [], "next_after": null});
    let (server, store) = connect(vec![(200, capabilities()), (200, none)]);
    assert_eq!(
        Sink::Remote(Box::new(store)).first_turn_stamp("c").unwrap(),
        None
    );
    let path = server.requests.lock().unwrap()[1].0.clone();
    assert_eq!(path, "POST /v1/conversation/read_turns HTTP/1.1");
}

#[test]
fn only_a_server_that_declares_records_is_given_records() {
    for (version, keeps) in [(json!(1), true), (json!(2), false), (Value::Null, false)] {
        let (server, store) = connect(vec![(200, declaring(version))]);
        let sink = Sink::Remote(Box::new(store));
        assert_eq!(sink.records().unwrap().is_some(), keeps);
        drop(sink);
        assert_eq!(server.requests.lock().unwrap().len(), 1);
    }
    let decoded = codec::turn(&stored()["turn"]).unwrap();
    assert_eq!(decoded.turn, 4);
}
