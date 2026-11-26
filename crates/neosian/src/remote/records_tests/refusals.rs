//! Refusals that are not a conflict's envelope (rulings R40 and R41): a
//! free-text `reason` under another code, a refusal whose body breaks off,
//! and a gateway's page in place of JSON.

use std::io::{BufRead, BufReader, Read, Write as _};
use std::net::TcpListener;
use std::thread::{self, JoinHandle};

use serde_json::json;

use super::{connected, declaring, envelope, part, refused};
use crate::remote::RemoteStore;

#[test]
fn only_a_conflict_answers_a_reason() {
    let invalid = json!({"conversation_id":"Bad Id","reason":"must match the id grammar"});
    let unsupported =
        json!({"conversation_id":"c","reason":"turn 3 holds a tool name over 1024 bytes"});
    let (_server, store) = connected(vec![
        (
            400,
            envelope("agent_conversation_id_invalid", "no", invalid.clone()),
        ),
        (
            400,
            envelope("agent_conversation_format_unsupported", "no", unsupported),
        ),
        (
            400,
            envelope(
                "agent_conversation_conflict",
                "no",
                json!({"conversation_id":"c","reason":"draft_gap","next_seq":2}),
            ),
        ),
    ]);
    let first = store.write_records("Bad Id", &part("d1")).unwrap_err();
    assert_eq!(first.reason(), None);
    // The free text is still there for whoever shows the refusal.
    assert_eq!(refused(first).details, invalid);
    let second = store.write_records("c", &part("d1")).unwrap_err();
    assert_eq!(second.reason(), None);
    let third = store.write_records("c", &part("d1")).unwrap_err();
    assert_eq!(third.reason(), Some("draft_gap"));
}

/// A server that answers each request with the bytes given and closes,
/// whatever length those bytes declare.
fn raw_server(answers: Vec<String>) -> (String, JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let thread = thread::spawn(move || {
        for answer in answers {
            let (mut stream, _) = listener.accept().unwrap();
            let mut reader = BufReader::new(&mut stream);
            let mut length = 0;
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                if line == "\r\n" {
                    break;
                }
                if let Some((name, value)) = line.split_once(':')
                    && name.eq_ignore_ascii_case("content-length")
                {
                    length = value.trim().parse().unwrap();
                }
            }
            reader.read_exact(&mut vec![0; length]).unwrap();
            let _ = stream.write_all(answer.as_bytes());
        }
    });
    (url, thread)
}

#[test]
fn a_refusal_whose_body_breaks_off_keeps_its_status_and_seconds() {
    let handshake = declaring(json!(1)).to_string();
    let head = "Content-Type: application/json\r\nConnection: close\r\n";
    let answers = vec![
        format!(
            "HTTP/1.1 200 OK\r\n{head}Content-Length: {}\r\n\r\n{handshake}",
            handshake.len()
        ),
        // 500 bytes declared, 13 sent, then the connection closes.
        format!(
            "HTTP/1.1 429 Too Many Requests\r\n{head}Retry-After: 9\r\nContent-Length: 500\r\n\r\n{{\"error\":{{\"co"
        ),
        // A 200 that breaks off is a transport fault: nothing is known of it.
        format!("HTTP/1.1 200 OK\r\n{head}Content-Length: 500\r\n\r\n{{\"draft\":"),
    ];
    let (url, thread) = raw_server(answers);
    let store = RemoteStore::connect(&url, "test-token").unwrap();
    let kept = refused(store.write_records("c", &part("d1")).unwrap_err());
    assert_eq!((kept.status, kept.retry_after), (429, Some(9)));
    assert_eq!(
        (kept.code, kept.message.as_str()),
        (None, "{\"error\":{\"co")
    );
    let lost = store.write_records("c", &part("d1")).unwrap_err();
    assert!(
        matches!(lost, crate::remote::records::Failure::Transport(_)),
        "{lost}"
    );
    thread.join().unwrap();
}

#[test]
fn a_gateways_page_is_a_refusal_with_the_start_of_its_text() {
    // 300 scalars of two and three bytes each behind the tag: the message
    // is cut by scalars, never inside one.
    let page = format!(
        "<html>413 Request Entity Too Large{}</html>",
        "é€".repeat(150)
    );
    let (_server, store) = connected(vec![
        (413, json!({ "$raw": page })),
        (502, json!({ "$raw": "\u{0}\u{1}bad gateway" })),
    ]);
    let kept = refused(store.write_records("c", &part("d1")).unwrap_err());
    // Not the full destination: that is 413 with the code `storage_exceeded`.
    assert_eq!(
        (kept.status, kept.code, kept.retry_after),
        (413, None, None)
    );
    assert_eq!(kept.message.chars().count(), 256);
    assert!(page.starts_with(&kept.message) && kept.details.is_null());
    let kept = refused(store.write_records("c", &part("d1")).unwrap_err());
    assert_eq!(
        (kept.status, kept.message.as_str()),
        (502, "\u{0}\u{1}bad gateway")
    );
}
