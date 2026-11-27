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

/// A login import's scope unit (the L1b slice) is the bytes this client
/// posts to `store/restore_scope`: its digest is theirs, not a digest of
/// the value encoded again.
#[test]
fn a_scope_unit_is_measured_by_the_bytes_restore_scope_posts() {
    use crate::store::{FileStore, Restored};
    use sha2::{Digest, Sha256};
    let home = crate::testing::Scratch::new("remote-scope-unit");
    let head = "---\nneosian_format: 1\nversion: 2\ncreated_at: '2026-10-01T00:00:00Z'\nupdated_at: '2026-10-02T00:00:00.5Z'\nactor: seed\norigin: früh ✓\nticket: 7\n---\n";
    home.plant("user%3Ame/documents/a.md", &format!("{head}zwei ✓\n"));
    let row = |version, content: &str| {
        json!({"neosian_format": 1, "path": "a", "version": version,
               "action": if version == 1 { "created" } else { "modified" },
               "content": content, "actor": "seed", "created_at": "2026-10-01T00:00:00Z",
               "redacted": false})
        .to_string()
    };
    let rows = format!("{}\n{}\n", row(1, "eins\n"), row(2, "zwei ✓\n"));
    home.plant("user%3Ame/versions/a.jsonl", &rows);
    let source = FileStore::open(home.path()).unwrap();
    let (unit, bytes) = crate::operator::transfer::units::scope(&source, "user:me").unwrap();
    let stub = Stub::replies(vec![(200, capabilities()), (200, json!({}))]);
    let remote = RemoteStore::connect(&stub.url, "test-token").unwrap();
    let document = source.read("user:me", "a").unwrap().unwrap();
    let documents = [Restored {
        path: "a".into(),
        content: document.content,
        envelope: document.envelope,
    }];
    let mut versions = State::history(&source, "user:me", None, None).unwrap();
    versions.sort_by_key(|row| row.version);
    remote
        .restore_scope("user:me", &documents, versions, &[])
        .unwrap();
    let raw = stub.raw.lock().unwrap()[1].clone().into_bytes();
    assert_eq!(raw, bytes);
    let digest: String = Sha256::digest(&raw)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    assert_eq!((unit.sha256, unit.bytes), (digest, raw.len() as u64));
    assert_eq!((unit.documents, unit.versions, unit.redactions), (1, 2, 0));
}
