//! Operator reads honor paging and negotiated erasure before any request.

use super::*;
use crate::store::time::Stamp;

#[test]
fn unsupported_erasure_never_sends_a_mutation_or_trail_request() {
    let stub = Stub::replies(vec![(200, capabilities())]);
    let store = RemoteStore::connect(&stub.url, "test-token").unwrap();
    for error in [
        store
            .redact_turns("session", None, None, Some("cli:local"))
            .unwrap_err(),
        store.turn_redactions(None, None, 500).unwrap_err(),
    ] {
        assert!(error.text().contains("does not implement Erasable"));
    }
    assert_eq!(stub.requests.lock().unwrap().len(), 1);
}

#[test]
fn ledgers_follow_continuations_and_bound_the_remaining_read() {
    let version = |n| {
        json!({
            "path": "note", "version": n, "action": "modified", "content": "kept",
            "actor": "opaque actor", "created_at": "2026-01-01T00:00:00Z",
            "redacted": false,
        })
    };
    let act = json!({"path":null,"actor":null,"created_at":"2026-01-01T00:00:00Z","count":2});
    let stub = Stub::replies(vec![
        (200, capabilities()),
        (
            200,
            json!({"versions":[version(3),version(2)],"next_cursor":"next"}),
        ),
        (
            200,
            json!({"versions":[version(1)],"next_cursor":"remaining"}),
        ),
        (200, json!({"redactions":[act],"next_cursor":"trail"})),
        (200, json!({"redactions":[act],"next_cursor":null})),
    ]);
    let store = RemoteStore::connect(&stub.url, "test-token").unwrap();
    let since = Stamp::parse("2026-01-01T02:00:00+02:00").unwrap();
    let rows = store.history("user:test", Some(since), Some(3)).unwrap();
    assert_eq!(
        rows.iter().map(|row| row.version).collect::<Vec<_>>(),
        [3, 2, 1]
    );
    assert_eq!(rows[0].actor.as_deref(), Some("opaque actor"));
    assert_eq!(store.redactions("user:test", None, None).unwrap().len(), 2);
    let requests = stub.requests.lock().unwrap();
    assert_eq!(requests[1].1["since"], "2026-01-01T00:00:00Z");
    assert_eq!(requests[2].1["cursor"], "next");
    assert_eq!(requests[2].1["limit"], 1);
    assert_eq!(requests[4].1["cursor"], "trail");
    assert!(requests[4].1["limit"].is_null());
}

#[test]
fn malformed_trail_numbers_fail_instead_of_losing_the_erasure_record() {
    let mut caps = capabilities();
    caps["erasable"] = json!(true);
    let stub = Stub::replies(vec![
        (200, caps),
        (
            200,
            json!({"redactions":[{"conversation_id":"session","turns":[0],"actor":null,"created_at":"2026-01-01T00:00:00Z"}]}),
        ),
    ]);
    let store = RemoteStore::connect(&stub.url, "test-token").unwrap();
    assert!(
        store
            .turn_redactions(None, None, 500)
            .unwrap_err()
            .text()
            .contains("malformed wire response: turns")
    );
}
