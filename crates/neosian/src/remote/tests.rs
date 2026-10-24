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
                let body = answer.to_string();
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
