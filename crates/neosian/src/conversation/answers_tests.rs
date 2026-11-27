//! A door shows only what it asked for (the T1fe rulings J1 and J3),
//! against a server made up here. A legacy history server's turn of
//! another conversation is refused by recall, continue, the left-off block
//! and the record search fallback, from one answer and from size pages, and
//! the turn of the conversation asked still passes. A records server's
//! refusal of the asked turn because records cannot carry it reads that
//! turn alone, and a damaged record's refusal stays a refusal.

use std::sync::Arc;

use serde_json::{Value, json};

use super::cli_search_records::{Door, run};
use super::handoff::continue_conversation;
use super::rank::Order;
use super::recall::{Ask, View, recall};
use super::turn_records::Kind;
use crate::record::context::{History, render_left_off};
use crate::record::landing::tests::http::{Reply, Server};
use crate::remote::RemoteStore;
use crate::state::records::HistoryReader;
use crate::wire::WIRE_VERSION;

const READ: &str = "malformed wire response: conversation/read_turns";
const SEARCH: &str = "malformed wire response: conversation/search_turns";
const TEMP_BUDGET: &str = "resource limit NEOSIAN_MAX_TEMP_BYTES exceeded: 9 > 8; select a smaller batch or raise this setting";
const CANNOT_CARRY: &str =
    "turn 2: message 1 has a call id longer than 1024 bytes, which records cannot carry";

fn handshake(records: bool, paging: bool) -> Value {
    let mut declared = json!({"wire_version": WIRE_VERSION, "client": "client:test",
        "pageable": true, "erasable": false, "supports_optimistic_concurrency": false});
    if records {
        declared["turn_records"] = json!({"version": 1});
    }
    if paging {
        declared["size_paging"] = json!({"version": 1});
    }
    declared
}

fn turn(id: &str, number: u64, text: &str) -> Value {
    let message =
        json!({"role":"user","content":text,"reasoning":null,"tool_calls":[],"tool_call_id":null});
    json!({"conversation_id":id,"turn":number,"messages":[message],
           "created_at":"2026-01-01T00:00:00Z","actor":null,"redacted":false})
}

fn refusal(code: &str, message: &str, details: Value) -> Reply {
    let error = json!({"code": code, "message": message, "details": details});
    (400, vec![], json!({ "error": error }))
}

/// A legacy server whose every turn read or search answers `turns`: in one
/// answer, or when `paging` in size pages after a refusal for its size.
fn legacy(turns: Vec<Value>, paging: bool) -> Server {
    Server::new(move |path, body| {
        let ok = |value: Value| Some((200, vec![], value));
        let sized = paging && body.get("page_bytes").is_none();
        match path {
            "/v1/capabilities" => ok(handshake(false, paging)),
            "/v1/memory/read" => ok(json!({"document": null})),
            "/v1/store/scopes" => ok(json!({"scopes": []})),
            "/v1/conversation/read_projections" => ok(json!({"entries": [], "next_after": null})),
            "/v1/memory/list_documents" => ok(json!({"entries": [{
                "path": "sessions/c", "version": 1, "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "redacted": false}], "next_cursor": null})),
            "/v1/conversation/read_turns" | "/v1/conversation/search_turns" if sized => {
                Some(refusal("value_error", TEMP_BUDGET, json!({})))
            }
            "/v1/conversation/read_turns" if paging => {
                ok(json!({"turns": turns, "next_after": null, "continuation": null}))
            }
            "/v1/conversation/read_turns" => ok(json!({"turns": turns, "next_after": null})),
            "/v1/conversation/search_turns" if paging => {
                ok(json!({"turns": turns, "continuation": null}))
            }
            "/v1/conversation/search_turns" => ok(json!({"turns": turns})),
            _ => None,
        }
    })
}

fn connect(server: &Server) -> RemoteStore {
    RemoteStore::connect(&server.url, "test-token").unwrap()
}

#[test]
fn recall_refuses_a_turn_of_another_conversation() {
    let ask = Ask::new("c", 1, None, None, None, None).unwrap();
    for paging in [false, true] {
        let server = legacy(vec![turn("other", 1, "elsewhere")], paging);
        let refused = recall(&connect(&server), None, &ask, "Conversation 'c'");
        assert_eq!(refused.unwrap_err().text(), READ, "{paging}");
        let server = legacy(vec![turn("c", 1, "here")], paging);
        let shown = recall(&connect(&server), None, &ask, "Conversation 'c'");
        assert!(shown.unwrap().unwrap().text.contains("USER: here"));
    }
}

#[test]
fn continue_refuses_a_turn_of_another_conversation() {
    for paging in [false, true] {
        let server = legacy(vec![turn("other", 1, "elsewhere")], paging);
        let store = connect(&server);
        let answer = continue_conversation(&store, None, "user:a", Some("c"), None, View::Light);
        let Err(error) = answer else {
            panic!("a refusal")
        };
        assert_eq!(error.text(), READ, "{paging}");
        let server = legacy(vec![turn("c", 1, "here")], paging);
        let store = connect(&server);
        let answer = continue_conversation(&store, None, "user:a", Some("c"), None, View::Light);
        let Ok(Ok(delivered)) = answer else {
            panic!("a delivery")
        };
        assert!(delivered.text.contains("[1] USER: here"));
    }
}

#[test]
fn the_left_off_block_refuses_a_turn_of_another_conversation() {
    for paging in [false, true] {
        let server = legacy(vec![turn("other", 1, "elsewhere")], paging);
        let store = connect(&server);
        let block = render_left_off(History::new(&store, None), "user:a", "", "startup", 4_000);
        assert_eq!(block.unwrap_err().text(), READ, "{paging}");
        let server = legacy(vec![turn("c", 1, "here")], paging);
        let store = connect(&server);
        let block = render_left_off(History::new(&store, None), "user:a", "", "startup", 4_000);
        assert!(block.unwrap().contains("[1] USER: here"));
    }
}

#[test]
fn the_record_search_fallback_refuses_a_hit_of_a_conversation_not_asked() {
    let terms = ["needle".to_string()];
    let asked = ["allowed".to_string()];
    for paging in [false, true] {
        for (id, refused) in [("excluded", true), ("allowed", false)] {
            let server = legacy(vec![turn(id, 1, "a needle")], paging);
            let store = connect(&server);
            let door = Door {
                store: &store,
                query: "needle",
                terms: &terms,
                named: Some(asked.to_vec()),
                ids: Some(&asked),
                limit: 5,
                order: Some(Order::Newest),
                since: None,
                before: None,
                bounds: (None, None),
                kinds: Some(vec![Kind::Prompt]),
                json: false,
            };
            let (mut out, mut err) = (Vec::new(), Vec::new());
            run(&door, None, &mut out, &mut err).unwrap();
            let (out, err) = (
                String::from_utf8(out).unwrap(),
                String::from_utf8(err).unwrap(),
            );
            if refused {
                assert_eq!(
                    (out.as_str(), err.trim()),
                    ("", format!("error: {SEARCH}").as_str())
                );
            } else {
                assert!(
                    out.contains("[allowed #1.0 prompt]") && err.is_empty(),
                    "{out}{err}"
                );
            }
        }
    }
}

/// A records server whose `read_records` refuses with `reason` and whose
/// legacy read answers turn 2 whole.
fn refusing(reason: &'static str) -> Server {
    Server::new(move |path, _| match path {
        "/v1/capabilities" => Some((200, vec![], handshake(true, false))),
        "/v1/conversation/read_records" => Some(refusal(
            "agent_conversation_format_unsupported",
            &format!("Unsupported turn format in conversation 'c': {reason}"),
            json!({"conversation_id": "c", "reason": reason}),
        )),
        "/v1/conversation/read_turns" => Some((
            200,
            vec![],
            json!({"turns": [turn("c", 2, "whole")], "next_after": null}),
        )),
        _ => None,
    })
}

#[test]
fn a_turn_records_cannot_carry_is_read_alone_and_a_damaged_one_is_refused() {
    let ask = Ask::new("c", 2, None, None, None, None).unwrap();
    let server = refusing(CANNOT_CARRY);
    let history = HistoryReader::Remote(Arc::new(connect(&server)));
    let records = history.records().unwrap().unwrap();
    let shown = recall(history.state().as_ref(), Some(&*records), &ask, "c");
    assert!(shown.unwrap().unwrap().text.contains("USER: whole"));
    let log = server.log.lock().unwrap().clone();
    let read: Vec<&Value> = log
        .iter()
        .filter(|(path, _)| path.ends_with("read_turns"))
        .map(|(_, body)| body)
        .collect();
    assert_eq!(
        read,
        [&json!({"conversation_id": "c", "after": 1, "limit": 1})]
    );
    let server = refusing("turn 2: its records file is missing");
    let history = HistoryReader::Remote(Arc::new(connect(&server)));
    let records = history.records().unwrap().unwrap();
    let refused = recall(history.state().as_ref(), Some(&*records), &ask, "c");
    assert!(
        refused
            .unwrap_err()
            .text()
            .starts_with("[agent_conversation_format_unsupported] ")
    );
    let log = server.log.lock().unwrap();
    assert!(!log.iter().any(|(path, _)| path.ends_with("read_turns")));
}
