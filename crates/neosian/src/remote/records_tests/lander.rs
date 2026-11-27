//! The lander's run against a server (DESIGN §40.54 items 15.2 and 17.4;
//! rulings E14, E15 and E16 of the T1e1 plan), through `run::store` and
//! the sink on the loopback stub: an unreadable answer to the fallback
//! holds the turn `unreadable`; after `agent_configuration_error` the
//! capability is read again, and a server still advertising it, or one
//! that cannot be reached, keeps the turn and stops the destination; a
//! refused header read of the first turn falls back to reading it whole.

use serde_json::{Value, json};

use super::declaring;
use crate::conversation::message::{Message, Role};
use crate::record::landing::rules::Stop;
use crate::record::landing::run::{Ended, Queued, UNREADABLE_ANSWER, store};
use crate::record::landing::sink::Sink;
use crate::record::landing::state::{Destination, Reason, State};
use crate::remote::selection::Selection;
use crate::remote::tests::{Stub, capabilities};
use crate::store::time::Stamp;
use crate::testing::Scratch;

const ID: &str = "c";

fn queued_state() -> State {
    State {
        draft: "p0123456789abcdef0123456789abcdef".into(),
        next_seq: 0,
        fingerprint: None,
        destination: Some(Destination::Remote {
            url: "http://127.0.0.1".into(),
            credential_sha256: "0".repeat(64),
        }),
        attempt: 0,
        closed_at: "2026-11-27T12:00:00.000000Z".into(),
        bytes: 1,
        agent: "claude-code".into(),
        actor: "claude-code:s".into(),
        session: "s".into(),
        conversation_id: ID.into(),
        scope: Some("user:me".into()),
        mount: Some("user".into()),
        provenance: None,
        parent: None,
        project: None,
    }
}

/// One queued turn of `answers`' server, landed by `run::store`.
fn landed(answers: Vec<(u16, Value)>) -> (Stub, Ended) {
    let server = Stub::replies(answers);
    let selection = Selection::Remote {
        url: server.url.clone(),
        token: "test-token".into(),
    };
    let scratch = Scratch::new("lander-remote");
    let file = scratch.path().join("1.state");
    let queued = Queued {
        id: ID,
        line: "{\"kind\": \"span\"}",
        file: &file,
    };
    let mut sink = Sink::open(&selection).unwrap();
    let messages = vec![
        Message::text(Role::User, "hello".into()),
        Message::text(Role::Assistant, "done".into()),
    ];
    let ended = store(
        &mut sink,
        &selection,
        &queued,
        &mut queued_state(),
        messages,
    );
    (server, ended.unwrap())
}

fn routes(server: &Stub) -> Vec<String> {
    let requests = server.requests.lock().unwrap();
    requests.iter().map(|(line, _)| line.clone()).collect()
}

fn configuration() -> (u16, Value) {
    let error = json!({"code": "agent_configuration_error", "message": "no records here",
        "details": {}});
    (400, json!({ "error": error }))
}

#[test]
fn an_unreadable_answer_to_the_fallback_holds_the_turn_unreadable() {
    let (server, ended) = landed(vec![(200, capabilities()), (200, json!({"turn": 3}))]);
    let Ended::Held(reason) = ended else {
        panic!("held, not {ended:?}")
    };
    assert_eq!(reason, Reason::unreadable(UNREADABLE_ANSWER.into()));
    assert_eq!(
        reason.text(),
        "{\"reason\":\"unreadable\",\"message\":\"a 2xx was answered but could not be read; it may be stored\"}\n"
    );
    assert_eq!(
        routes(&server)[1],
        "POST /v1/conversation/append_turn HTTP/1.1"
    );
}

#[test]
fn a_capability_still_advertised_after_a_configuration_refusal_keeps_the_turn() {
    let records = declaring(json!(1));
    let answers = vec![(200, records.clone()), configuration(), (200, records)];
    let (server, ended) = landed(answers);
    let Ended::Kept(Stop::Wait {
        seconds: 60,
        failing: true,
        message,
    }) = ended
    else {
        panic!("kept, not {ended:?}")
    };
    assert_eq!(message, "[agent_configuration_error] no records here");
    assert_eq!(routes(&server).len(), 3);
}

#[test]
fn a_capability_gone_after_a_configuration_refusal_sends_the_turn_whole() {
    let turn = json!({"turn": {"conversation_id": ID, "turn": 1, "messages": [
        {"role": "user", "content": "hello", "reasoning": null, "tool_calls": [], "tool_call_id": null},
        {"role": "assistant", "content": "done", "reasoning": null, "tool_calls": [], "tool_call_id": null}],
        "created_at": "2026-11-27T12:00:00Z", "actor": "client:test/claude-code:s", "redacted": false}});
    let answers = vec![
        (200, declaring(json!(1))),
        configuration(),
        (200, capabilities()),
        (200, turn),
    ];
    let (server, ended) = landed(answers);
    assert!(
        matches!(ended, Ended::Stored(ref turn) if turn.turn == 1),
        "{ended:?}"
    );
    assert_eq!(
        routes(&server)[3],
        "POST /v1/conversation/append_turn HTTP/1.1"
    );
}

#[test]
fn a_server_gone_when_the_capability_is_read_again_keeps_the_turn() {
    // No third answer: the stub has stopped listening, so opening fails.
    let (_, ended) = landed(vec![(200, declaring(json!(1))), configuration()]);
    assert!(
        matches!(ended, Ended::Kept(Stop::Wait { failing: true, .. })),
        "{ended:?}"
    );
}

#[test]
fn a_refused_header_read_falls_back_to_the_first_turn_read_whole() {
    let why = "turn 1: a tool call id holds more than 1024 bytes";
    let refused = json!({"error": {"code": "agent_conversation_format_unsupported",
        "message": format!("Unsupported turn format in conversation 'c': {why}"),
        "details": {"conversation_id": ID, "reason": why}}});
    let whole = json!({"turns": [{"conversation_id": ID, "turn": 1, "messages": [],
        "created_at": "2026-11-20T08:00:00Z", "actor": null, "redacted": false}],
        "next_after": null});
    let server = Stub::replies(vec![
        (200, declaring(json!(1))),
        (400, refused),
        (200, whole),
    ]);
    let store = crate::remote::RemoteStore::connect(&server.url, "test-token").unwrap();
    let sink = Sink::Remote(Box::new(store));
    let first = sink.first_turn_stamp(ID).unwrap();
    assert_eq!(first, Some(Stamp::parse("2026-11-20T08:00:00Z").unwrap()));
    let paths = routes(&server);
    assert_eq!(paths[1], "POST /v1/conversation/read_records HTTP/1.1");
    assert_eq!(paths[2], "POST /v1/conversation/read_turns HTTP/1.1");
}
