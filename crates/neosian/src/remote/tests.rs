//! Real loopback exchanges: handshake ordering, paging, refusals and
//! transport failures. No provider or application server is involved.

mod operator;

use super::*;
use crate::state::State;
use crate::store::grammar::Actor;
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

struct Stub {
    url: String,
    requests: Arc<Mutex<Vec<(String, Value)>>>,
    thread: Option<JoinHandle<()>>,
}

impl Stub {
    fn new(answers: Vec<(u16, Value)>, delay: Duration) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let recorded = requests.clone();
        let thread = thread::spawn(move || {
            for (status, answer) in answers {
                let deadline = Instant::now() + Duration::from_secs(5);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                            assert!(Instant::now() < deadline, "missing request");
                            thread::sleep(Duration::from_millis(2));
                        }
                        Err(error) => panic!("{error}"),
                    }
                };
                // Accepted sockets inherit nonblocking mode on macOS.
                // Only the accept loop polls; the request reader blocks.
                stream.set_nonblocking(false).unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut reader = BufReader::new(&mut stream);
                let mut request = String::new();
                reader.read_line(&mut request).unwrap();
                let mut length = 0;
                let mut authorized = false;
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" {
                        break;
                    }
                    if let Some((name, value)) = line.split_once(':') {
                        if name.eq_ignore_ascii_case("content-length") {
                            length = value.trim().parse().unwrap();
                        }
                        if name.eq_ignore_ascii_case("authorization") {
                            authorized = value.trim() == "Bearer test-token";
                        }
                    }
                }
                assert!(authorized);
                let mut body = vec![0; length];
                reader.read_exact(&mut body).unwrap();
                let body = if body.is_empty() {
                    Value::Null
                } else {
                    serde_json::from_slice(&body).unwrap()
                };
                recorded
                    .lock()
                    .unwrap()
                    .push((request.trim().to_string(), body));
                thread::sleep(delay);
                if status == 0 {
                    continue;
                } // disconnect after receiving a mutation
                // `{"$raw": text}` answers `text` verbatim, for bodies that are not JSON.
                let body = match answer.get("$raw").and_then(Value::as_str) {
                    Some(raw) => raw.to_string(),
                    None => answer.to_string(),
                };
                let _ = write!(
                    stream,
                    "HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
            }
        });
        Self {
            url,
            requests,
            thread: Some(thread),
        }
    }
    fn replies(answers: Vec<(u16, Value)>) -> Self {
        Self::new(answers, Duration::ZERO)
    }
}
impl Drop for Stub {
    fn drop(&mut self) {
        self.thread.take().unwrap().join().unwrap();
    }
}
fn capabilities() -> Value {
    json!({"wire_version":WIRE_VERSION,"client":"client:test","pageable":true,"erasable":false,"supports_optimistic_concurrency":false})
}
fn failure(result: Result<RemoteStore, Error>) -> String {
    crate::conversation::Error::from(result.err().expect("must refuse")).text()
}

#[test]
fn handshake_refuses_skew_or_credentials_before_a_store_call() {
    for (status, body, text) in [
        (200, json!({"wire_version":999}), "wire version skew"),
        (200, json!({}), "server speaks None"),
        (401, json!({}), "rejected the bearer token"),
    ] {
        let server = Stub::replies(vec![(status, body)]);
        assert!(failure(RemoteStore::connect(&server.url, "test-token")).contains(text));
        assert_eq!(server.requests.lock().unwrap().len(), 1);
    }
}

#[test]
fn connection_keeps_identitycapabilities_and_url_prefix() {
    let server = Stub::replies(vec![(200, capabilities()), (200, json!({"document":null}))]);
    let store = RemoteStore::connect(&format!("{}/prefix/", server.url), "test-token").unwrap();
    assert_eq!(store.client(), Some("client:test"));
    assert_eq!(store.capabilities["supports_optimistic_concurrency"], false);
    assert!(store.read("user:a", "absent").unwrap().is_none());
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests[0].0, "GET /prefix/v1/capabilities HTTP/1.1");
    assert_eq!(requests[1].0, "POST /prefix/v1/memory/read HTTP/1.1");
}

#[test]
fn pages_follow_opaque_cursors_and_decrement_limits() {
    let row = json!({"path":"x","version":1,"action":"created","content":"é", "actor":null,"created_at":"2026-01-01T00:00:00Z","redacted":false});
    let server = Stub::replies(vec![
        (200, capabilities()),
        (
            200,
            json!({"versions":[row.clone()],"next_cursor":"opaque"}),
        ),
        (200, json!({"versions":[row],"next_cursor":null})),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    assert_eq!(store.versions("user:a", "x", 2).unwrap().len(), 2);
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests[2].1["cursor"], "opaque");
    assert_eq!(requests[2].1["limit"], 1);
}

#[test]
fn errors_keep_details_and_non_envelopes_remain_transport_failures() {
    let server = Stub::replies(vec![
        (200, capabilities()),
        (
            400,
            json!({"error":{"code":"memory_conflict","message":"conflict","details":{"scope":"user:a","path":"x","reason":"version_mismatch","expected_version":1,"actual_version":2}}}),
        ),
        (
            403,
            json!({"error":{"code":"forbidden","message":"outside allowance","details":{}}}),
        ),
        (
            400,
            json!({"error":{"code":"future_code","message":"future","details":{"new":42}}}),
        ),
        (500, json!("not an envelope")),
        (200, json!([])),
        (302, json!({})),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    let Err(Error::Memory(error)) = store.read("user:a", "x") else {
        panic!("typed error")
    };
    assert_eq!(error.code, "memory_conflict");
    assert_eq!(error.details["actual_version"], 2);
    let Err(Error::Memory(error)) = store.read("user:a", "x") else {
        panic!("denial")
    };
    assert_eq!(error.code, "neosian_error");
    let Err(Error::Memory(error)) = store.read("user:a", "x") else {
        panic!("unknown")
    };
    assert_eq!(error.details["new"], 42);
    assert!(matches!(store.read("user:a", "x"), Err(Error::Io(_))));
    assert!(matches!(store.read("user:a", "x"), Err(Error::Value(_))));
    assert!(matches!(store.read("user:a", "x"), Err(Error::Io(_))));
    assert_eq!(server.requests.lock().unwrap().len(), 7);
}

#[test]
fn a_lost_mutation_is_never_retried_and_delete_can_lack_a_receipt() {
    let server = Stub::replies(vec![
        (200, capabilities()),
        (200, json!({"deleted":true})),
        (200, json!({"versions":[],"next_cursor":null})),
        (0, Value::Null),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    let actor = Actor::parse("cli:test").unwrap();
    let deleted = store.delete("user:a", "x", &actor).unwrap();
    assert!(deleted.removed);
    assert_eq!(deleted.version, None);
    assert!(matches!(
        store.write("user:a", "x", "new", &actor, None),
        Err(Error::Io(_))
    ));
    assert_eq!(server.requests.lock().unwrap().len(), 4);
}

#[test]
fn timeout_is_a_transport_failure() {
    let server = Stub::new(vec![(200, capabilities())], Duration::from_millis(100));
    assert!(matches!(
        RemoteStore::with_timeout(&server.url, "test-token", Duration::from_millis(20)),
        Err(Error::Io(_))
    ));
}

#[test]
fn document_decoder_keeps_nested_extra_and_refuses_malformed_fields() {
    let data = json!({"content":"é\n","version":2,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z","actor":"client:test/cli:me","redacted":false,"extra":{"nested":{"a":[1,true,null]}}});
    let document = codec::document(&data).unwrap();
    assert_eq!(document.content, "é\n");
    assert_eq!(
        document.envelope.extra,
        vec!["\"nested\": {\"a\":[1,true,null]}".to_string()]
    );
    assert!(codec::document(&json!({"version":"2"})).is_err());
}

fn turn(number: u64) -> Value {
    let message =
        json!({"role":"user","content":"x","reasoning":null,"tool_calls":[],"tool_call_id":null});
    json!({"conversation_id":"c","turn":number,"messages":[message],"created_at":"2026-01-01T00:00:00Z","actor":null,"redacted":false})
}

fn paging() -> Value {
    let mut capabilities = capabilities();
    capabilities["size_paging"] = json!({"version": 1});
    capabilities
}

#[test]
fn every_request_is_2_16_0s_whether_or_not_the_server_pages() {
    for capabilities in [capabilities(), paging()] {
        let server = Stub::replies(vec![
            (200, capabilities),
            (200, json!({"turns":[turn(1)]})),
            (200, json!({"turns":[turn(1)],"next_after":null})),
        ]);
        let store = RemoteStore::connect(&server.url, "test-token").unwrap();
        store.search_turns(&["x".into()], None, 5).unwrap();
        store.read_turns("c", 0, None).unwrap();
        let requests = server.requests.lock().unwrap();
        assert_eq!(
            requests[1].1,
            json!({"query":"x","conversations":null,"limit":5})
        );
        assert_eq!(
            requests[2].1,
            json!({"conversation_id":"c","after":0,"limit":null})
        );
    }
}

#[test]
fn a_200_that_is_not_json_is_malformed() {
    let server = Stub::replies(vec![
        (200, paging()),
        (200, json!({"$raw": "{\"turns\":[\n{\"error\":{}}"})),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    // Whatever follows a line feed: a server never ends a 200 with an error.
    let refused = store.read_turns("c", 0, None).err().unwrap();
    assert!(matches!(refused, crate::conversation::Error::Value(_)));
}

fn refusal(message: &str) -> Value {
    json!({"error":{"code":"value_error","message":message,"details":{}}})
}

const TEMP_BUDGET: &str = "resource limit NEOSIAN_MAX_TEMP_BYTES exceeded: 9 > 8; select a smaller batch or raise this setting";
const ANSWER_BUDGET: &str = "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: 3 > 2; select a smaller batch or raise this setting";

#[test]
fn a_refusal_for_size_is_asked_again_in_pages_only_by_a_paging_server() {
    let server = Stub::replies(vec![
        (200, paging()),
        (400, refusal(TEMP_BUDGET)),
        (
            200,
            json!({"turns":[turn(1)],"next_after":null,"continuation":"next"}),
        ),
        (
            200,
            json!({"turns":[turn(2)],"next_after":null,"continuation":null}),
        ),
        (400, refusal(TEMP_BUDGET)),
        (200, json!({"turns":[turn(3)],"continuation":null})),
        (
            403,
            json!({"error":{"code":"forbidden","message":"no","details":{}}}),
        ),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    let turns = store.read_turns("c", 0, None).unwrap();
    assert_eq!(turns.iter().map(|t| t.turn).collect::<Vec<_>>(), [1, 2]);
    let found = store.search_turns(&["x".into()], None, 5).unwrap();
    assert_eq!(found.iter().map(|t| t.turn).collect::<Vec<_>>(), [3]);
    // Any other refusal is reported as it is, with no second request.
    assert!(store.read_turns("c", 0, None).is_err());
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 7);
    // The first request is always 2.16.0's.
    assert_eq!(
        requests[1].1,
        json!({"conversation_id":"c","after":0,"limit":null})
    );
    assert_eq!(
        requests[2].1,
        json!({"conversation_id":"c","after":0,"limit":null,"page_bytes":983_040})
    );
    assert_eq!(requests[3].1["continuation"], "next");
    assert_eq!(
        requests[4].1,
        json!({"query":"x","conversations":null,"limit":5})
    );
    assert_eq!(requests[5].1["page_bytes"], 983_040);
}

#[test]
fn a_failed_paged_retry_reports_the_first_refusal_and_old_servers_get_none() {
    let server = Stub::replies(vec![
        (200, paging()),
        (400, refusal(TEMP_BUDGET)),
        (400, refusal(ANSWER_BUDGET)),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    let Err(crate::conversation::Error::Value(text)) = store.read_turns("c", 0, None) else {
        panic!("a refusal")
    };
    assert_eq!(text, TEMP_BUDGET);
    assert_eq!(server.requests.lock().unwrap().len(), 3);
    drop(server);
    let old = Stub::replies(vec![(200, capabilities()), (400, refusal(TEMP_BUDGET))]);
    let store = RemoteStore::connect(&old.url, "test-token").unwrap();
    assert!(store.search_turns(&["x".into()], None, 5).is_err());
    assert_eq!(old.requests.lock().unwrap().len(), 2);
}

#[test]
fn a_memory_listing_refused_for_size_is_asked_again_with_fewer_rows() {
    let row = json!({"path":"x","version":1,"action":"created","content":"é", "actor":null,"created_at":"2026-01-01T00:00:00Z","redacted":false});
    let server = Stub::replies(vec![
        (200, paging()),
        (400, refusal(ANSWER_BUDGET)),
        (
            200,
            json!({"versions":[row.clone(), row.clone()],"next_cursor":"c"}),
        ),
        (
            200,
            json!({"versions":[row.clone(), row],"next_cursor":null}),
        ),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    assert_eq!(store.versions("user:a", "x", 4).unwrap().len(), 4);
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests[1].1["limit"], 4);
    assert_eq!(requests[2].1["limit"], 2);
    assert_eq!(requests[2].1["cursor"], Value::Null);
    assert_eq!(requests[3].1["cursor"], "c");
    assert_eq!(requests[3].1["limit"], 2);
}

#[test]
fn the_candidate_bound_is_never_asked_again_and_its_format_is_pinned() {
    // #419: no smaller answer counts fewer candidates. The client recognizes
    // the refusal by the documented prefix, so its exact text is pinned here.
    let broad = crate::limits::broad(5, "turns", "the words");
    assert_eq!(
        broad,
        "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than 5 turns hold a \
         search term; narrow the words"
    );
    let server = Stub::replies(vec![(200, paging()), (400, refusal(&broad))]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    let Err(crate::conversation::Error::Value(text)) = store.search_turns(&["x".into()], None, 5)
    else {
        panic!("the bound refuses")
    };
    assert_eq!(text, broad);
    assert_eq!(server.requests.lock().unwrap().len(), 2);
}

#[test]
fn a_retry_that_fails_for_another_reason_reports_itself() {
    let forbidden = json!({"error":{"code":"forbidden","message":"revoked","details":{}}});
    let row = json!({"path":"x","version":1,"action":"created","content":"é", "actor":null,"created_at":"2026-01-01T00:00:00Z","redacted":false});
    let server = Stub::replies(vec![
        (200, paging()),
        (400, refusal(TEMP_BUDGET)),
        (403, forbidden.clone()),
        (400, refusal(TEMP_BUDGET)),
        (
            200,
            json!({"versions":[row.clone(), row.clone()],"next_cursor":"c"}),
        ),
        (403, forbidden),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    // A size page refused as forbidden reports that, not the size refusal.
    let Err(error) = store.read_turns("c", 0, None) else {
        panic!("forbidden")
    };
    assert!(error.text().contains("revoked"), "{}", error.text());
    // A listing whose smaller page answered forgets the size refusal: the
    // next page's own failure is what the caller sees.
    let Err(Error::Memory(error)) = store.versions("user:a", "x", 4) else {
        panic!("forbidden")
    };
    assert_eq!(error.message, "revoked");
    assert_eq!(server.requests.lock().unwrap().len(), 6);
}

#[test]
fn a_value_error_keeps_its_message_on_initial_and_smaller_pages() {
    let broad = crate::limits::broad(5, "turns", "the words");
    let mut capabilities = paging();
    capabilities["history_ranking"] = json!({"version": 1});
    let server = Stub::replies(vec![
        (200, capabilities),
        (400, refusal(TEMP_BUDGET)),
        (400, refusal(&broad)),
        (400, refusal("invalid cursor")),
        (400, refusal(TEMP_BUDGET)),
        (400, refusal("cursor no longer valid")),
        (400, refusal(TEMP_BUDGET)),
        (
            200,
            json!({"turns":[turn(1)],"scores":[7],"order":"relevance","continuation":"next"}),
        ),
        (400, refusal(crate::conversation::page::STALE)),
    ]);
    let store = RemoteStore::connect(&server.url, "test-token").unwrap();
    let search = crate::conversation::rank::Request {
        terms: vec!["x".into()],
        ids: None,
        limit: 5,
        order: crate::conversation::rank::Order::Relevance,
        since: None,
        before: None,
    };
    assert_eq!(store.rank_turns_wire(&search).err().unwrap().text(), broad);
    for expected in ["invalid cursor", "cursor no longer valid"] {
        let Err(Error::Value(message)) = store.versions("user:a", "x", 4) else {
            panic!("invalid listing cursor");
        };
        assert_eq!(message, expected);
    }
    // A relevance continuation that went stale retains its documented
    // diagnostic for a search split only because the answer was too large.
    assert_eq!(
        store.rank_turns_wire(&search).err().unwrap().text(),
        TEMP_BUDGET
    );
    assert_eq!(server.requests.lock().unwrap().len(), 9);
}
