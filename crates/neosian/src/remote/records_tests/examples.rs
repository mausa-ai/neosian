//! The examples of wire.md's "Turn records version 1" as the client's
//! contract. Each request and answer is taken from the embedded page, the
//! places it shortens are filled in, and the request this client sends
//! must equal it byte for byte once the page's line breaks are removed.
//! The answer the page prints is then decoded.

use serde_json::Value;

use super::{connected, record};
use crate::conversation::message::Role;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    Form, Header, Kind, RecordRead, Restore, Staged, State, TurnRecord, Write,
};
use crate::conversation::{Projection, Redaction};
use crate::remote::RemoteStore;
use crate::remote::records::{RecordPage, RecordQuery};
use crate::remote::tests::Stub;
use crate::store::time::Stamp;

const WIRE: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/docs/wire.md"));

/// The `nth` example of `route` as the page prints it: request and answer.
fn printed(route: &str, nth: usize) -> (&'static str, &'static str) {
    let head = format!("POST /v1/{route}\n");
    let (at, _) = WIRE.match_indices(&head).nth(nth).expect("an example");
    let (request, rest) = WIRE[at + head.len()..].split_once("\n\n200 ").unwrap();
    let ends = ["\n\n", "\n```"].map(|end| rest.find(end).unwrap());
    (request, &rest[..ends[0].min(ends[1])])
}

/// The page's text with each shortened place filled, as compact JSON in
/// the page's key order.
fn filled(text: &str, places: &[(&str, &str)]) -> String {
    let mut text = text.to_string();
    for (short, whole) in places {
        assert!(text.contains(short), "the page no longer prints {short:?}");
        text = text.replace(short, whole);
    }
    serde_json::from_str::<Value>(&text).unwrap().to_string()
}

/// A stub that answers the example, the store, and the request expected.
fn example(
    route: &str,
    nth: usize,
    request: &[(&str, &str)],
    answer: &[(&str, &str)],
) -> (Stub, RemoteStore, String) {
    let (asked, answered) = printed(route, nth);
    let answer = serde_json::json!({"$raw": filled(answered, answer)});
    let (server, store) = connected(vec![(200, answer)]);
    (server, store, filled(asked, request))
}

fn sent(server: &Stub) -> String {
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 2, "one request for one answer");
    server.raw.lock().unwrap()[1].clone()
}

fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

fn call() -> TurnRecord {
    TurnRecord {
        content: Some(Form::None),
        call: Some(0),
        call_id: Some("toolu_01".into()),
        name: Some("Bash".into()),
        ..record(
            1,
            1,
            Role::Assistant,
            Kind::ToolCall,
            "{\"command\":\"make test\"}",
        )
    }
}

fn result() -> TurnRecord {
    TurnRecord {
        call_id: Some("toolu_01".into()),
        size: 812_345,
        ..record(2, 2, Role::Tool, Kind::ToolResult, "...")
    }
}

#[test]
fn a_write_in_two_parts_is_the_pages_example() {
    let places = [(",\n  ... seq 3 to 8, chunks 1 to 6 ...]", "]")];
    let (server, store, expected) = example("conversation/write_records", 0, &places, &[]);
    let opening = Write {
        draft: "d7c1e0b2a9f44e57".into(),
        records: vec![
            record(0, 0, Role::User, Kind::Prompt, "run the tests"),
            call(),
            result(),
        ],
        turn_bytes: Some(812_400),
        actor: Some("claude-code:7f3a".into()),
        // Never sent: the server stamps under the token's client.
        stamp: Some("client:default".into()),
        ..Write::default()
    };
    let written = store.write_records("cc-7f3a", &opening).unwrap();
    assert_eq!(sent(&server), expected);
    assert_eq!((written.state, written.next_seq), (State::Open, 9));
    assert_eq!((written.stored_bytes, written.freed_bytes), (458_588, 0));
    assert_eq!(
        (written.draft.as_str(), written.turn),
        ("d7c1e0b2a9f44e57", None)
    );

    let places = [(" ... seq 9 to 14, chunks 7 to 12 ...,", "")];
    let (server, store, expected) = example("conversation/write_records", 1, &places, &[]);
    let seal = Write {
        draft: "d7c1e0b2a9f44e57".into(),
        records: vec![record(
            15,
            3,
            Role::Assistant,
            Kind::Reply,
            "All 412 tests pass.",
        )],
        seal: true,
        actor: Some("claude-code:7f3a".into()),
        ..Write::default()
    };
    let written = store.write_records("cc-7f3a", &seal).unwrap();
    assert_eq!(sent(&server), expected);
    assert_eq!((written.state, written.next_seq), (State::Sealed, 16));
    assert_eq!(
        (written.stored_bytes, written.freed_bytes),
        (359_889, 1_054)
    );
    let header = Header {
        turn: 42,
        created_at: stamp("2026-11-21T14:03:07.512Z"),
        actor: Some("client:default/claude-code:7f3a".into()),
        redacted: false,
        records: 16,
        bytes: 812_400,
    };
    assert_eq!(written.turn, Some(header));
}

const HEADER: &str = r#"{"turn":42,"created_at":"2026-11-21T14:03:07.512000Z","actor":"client:default/claude-code:7f3a","redacted":false,"records":16,"bytes":812400}"#;

fn chunk(seq: u64) -> String {
    let chunk = seq - 2;
    format!(
        r#"{{"turn":42,"seq":{seq},"message":2,"role":"tool","kind":"tool_result","content":null,"block":null,"call":null,"call_id":"toolu_01","name":null,"chunk":{chunk},"size":812345,"text":"t{seq}","tail":null,"truncated":false}}"#
    )
}

#[test]
fn a_read_under_a_view_and_a_piece_whole_are_the_pages_examples() {
    let answer = [
        (
            r#""seq":1, ... ,"kind":"tool_call", ... ,"#,
            r#""seq":1,"message":1,"role":"assistant","kind":"tool_call","content":"none","block":null,"call":0,"call_id":"toolu_01","name":"Bash","chunk":0,"size":23,"#,
        ),
        (
            r#""seq":15, ... ,"kind":"reply", ... ,"#,
            r#""seq":15,"message":3,"role":"assistant","kind":"reply","content":"text","block":null,"call":null,"call_id":null,"name":null,"chunk":0,"size":19,"#,
        ),
    ];
    let (server, store, expected) = example("conversation/read_records", 0, &[], &answer);
    let view = RecordRead {
        turn: Some(42),
        kinds: Some(vec![
            Kind::Prompt,
            Kind::Reply,
            Kind::ToolCall,
            Kind::ToolResult,
        ]),
        heads: vec![(Kind::ToolResult, 200)],
        ..RecordRead::default()
    };
    let mut pages: Vec<RecordPage> = Vec::new();
    let mut keep = |page| {
        pages.push(page);
        Ok(true)
    };
    store.read_records("cc-7f3a", &view, &mut keep).unwrap();
    assert_eq!(sent(&server), expected);
    let page = pages.pop().unwrap();
    assert_eq!((page.turns.len(), page.continuation), (1, None));
    assert_eq!((page.turns[0].turn, page.turns[0].bytes), (42, 812_400));
    let seqs: Vec<u64> = page.records.iter().map(|one| one.record.seq).collect();
    assert_eq!(seqs, [0, 1, 2, 15]);
    assert_eq!(page.records[1].record, call());
    let head = &page.records[2];
    assert_eq!(
        (head.turn, head.truncated, head.record.size),
        (42, true, 812_345)
    );
    assert_eq!(head.record.text, "cargo test --locked ...");
    let tail = "... test result: ok. 412 passed; 0 failed";
    assert_eq!(head.tail.as_deref(), Some(tail));
    assert_eq!(
        (page.records[3].tail.as_deref(), page.records[3].truncated),
        (None, false)
    );

    let (two, three, four) = (chunk(2), chunk(3), chunk(4));
    let answer = [
        ("{...turn 42...}", HEADER),
        ("{...seq 2...}", two.as_str()),
        ("{...seq 3...}", three.as_str()),
        ("{...seq 4...}", four.as_str()),
    ];
    let (server, store, expected) = example("conversation/read_records", 1, &[], &answer);
    let piece = RecordRead {
        turn: Some(42),
        seq: Some(2),
        piece: true,
        page_bytes: 262_144,
        ..RecordRead::default()
    };
    // The page names a continuation; the visitor stops, so nothing more
    // is asked.
    let mut stop = |page: RecordPage| {
        pages.push(page);
        Ok(false)
    };
    store.read_records("cc-7f3a", &piece, &mut stop).unwrap();
    assert_eq!(sent(&server), expected);
    let page = pages.pop().unwrap();
    let texts: Vec<&str> = page
        .records
        .iter()
        .map(|one| one.record.text.as_str())
        .collect();
    assert_eq!(texts, ["t2", "t3", "t4"]);
    assert_eq!(
        page.continuation.as_deref(),
        Some("WyJyZWNvcmRzLXYxL3JlYWQi...")
    );
}

#[test]
fn a_search_is_the_pages_example() {
    let (server, store, expected) = example("conversation/search_records", 0, &[], &[]);
    let query = RecordQuery {
        query: "failed assertion".into(),
        conversations: Some(vec!["cc-7f3a".into()]),
        kinds: Some(vec![Kind::ToolResult]),
        order: Some(Order::Relevance),
        limit: Some(5),
        ..RecordQuery::default()
    };
    let hits = store.search_records(&query).unwrap();
    assert_eq!(sent(&server), expected);
    assert_eq!(hits.len(), 1);
    let hit = &hits[0];
    assert_eq!(
        (hit.conversation_id.as_str(), hit.turn, hit.seq),
        ("cc-7f3a", 42, 9)
    );
    assert_eq!(
        (hit.message, hit.kind, hit.size),
        (2, Kind::ToolResult, 812_345)
    );
    assert_eq!(
        (hit.call_id.as_deref(), hit.name.as_deref()),
        (Some("toolu_01"), Some("Bash"))
    );
    assert_eq!(hit.created_at, stamp("2026-11-21T14:03:07.512Z"));
    assert_eq!(
        hit.actor.as_deref(),
        Some("client:default/claude-code:7f3a")
    );
    assert_eq!((hit.excerpt.0, hit.score), (31_507, Some(575_364)));
    assert!(hit.excerpt.1.contains("failed assertion: left == right"));
}

const SHORT: &str = ",...}";
const WHOLE: &str = r#","message":0,"role":"user","kind":"prompt","chunk":0,"size":1,"text":"x"}"#;

fn restored_record(turn: u64, seq: u64) -> (u64, TurnRecord) {
    let content = None;
    (
        turn,
        TurnRecord {
            content,
            ..record(seq, 0, Role::User, Kind::Prompt, "x")
        },
    )
}

fn header(turn: u64, at: &str, redacted: bool, records: u64, bytes: u64) -> Header {
    Header {
        turn,
        created_at: stamp(at),
        actor: Some("claude-code:7f3a".into()),
        redacted,
        records,
        bytes,
    }
}

#[test]
fn a_restore_in_two_parts_is_the_pages_example() {
    let places = [(SHORT, WHOLE)];
    let id = "iecf9f710d465bc0ea0fe2fbba8915596";
    let (server, store, expected) = example("store/restore_records", 0, &places, &[]);
    let first = Restore {
        restore: id.into(),
        turns: vec![header(1, "2026-10-02T09:15:00.000001Z", false, 3, 1_532)],
        records: (0..3).map(|seq| restored_record(1, seq)).collect(),
        ..Restore::default()
    };
    let restored = store.restore_records("cc-7f3a", &first).unwrap();
    assert_eq!(sent(&server), expected);
    assert_eq!(
        (restored.restore.as_str(), restored.state),
        (id, Staged::Open)
    );
    let position = restored.position.unwrap();
    assert_eq!(
        (position.turn, position.seq, position.complete),
        (1, 3, true)
    );
    assert_eq!(
        (restored.stored_bytes, restored.freed_bytes, restored.digest),
        (4_002, 0, None)
    );

    let (server, store, expected) = example("store/restore_records", 1, &places, &[]);
    let second = Restore {
        restore: id.into(),
        turns: vec![
            header(2, "2026-10-02T09:20:11Z", true, 0, 0),
            header(3, "2026-10-02T09:31:40Z", false, 1, 44),
        ],
        records: vec![restored_record(3, 0)],
        projections_from: Some(0),
        projections: vec![Projection {
            turn: 3,
            kind: "epoch".into(),
            text: "set up the cache".into(),
            span: 3,
        }],
        redactions_from: Some(0),
        redactions: vec![Redaction {
            conversation_id: "cc-7f3a".into(),
            turns: vec![2],
            actor: Some("client:default/ops".into()),
            created_at: stamp("2026-10-05T08:00:00Z"),
        }],
        commit: true,
        discard: false,
    };
    let restored = store.restore_records("cc-7f3a", &second).unwrap();
    assert_eq!(sent(&server), expected);
    assert_eq!(restored.state, Staged::Committed);
    let position = restored.position.unwrap();
    assert_eq!(
        (position.turn, position.seq, position.complete),
        (3, 1, true)
    );
    assert_eq!((position.projections, position.redactions), (1, 1));
    assert_eq!(
        (restored.stored_bytes, restored.freed_bytes),
        (2_077, 1_071)
    );
    let digest = "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32";
    assert_eq!(restored.digest.as_deref(), Some(digest));
}

#[test]
fn a_sweep_is_the_pages_example_and_a_probe_names_two_ids() {
    let (server, store, expected) = example("store/sweep_records", 0, &[], &[]);
    let page = store.sweep_records(2, None, true).unwrap();
    assert_eq!(sent(&server), expected);
    let swept: Vec<_> = page.conversations.iter().collect();
    assert_eq!(swept.len(), 2);
    assert_eq!(
        (swept[0].conversation_id.as_str(), swept[0].drafts),
        ("w-11112222-0a1b", 1)
    );
    assert_eq!((swept[0].restores, swept[0].freed_bytes), (0, 4_216_034));
    assert_eq!((swept[1].restores, swept[1].freed_bytes), (1, 38_977_511));
    let cursor = "WyJzd2VlcC12MSIsInctMTExMTIyMjItN2M5ZCJd";
    assert_eq!(
        (page.next_cursor.as_deref(), page.write),
        (Some(cursor), true)
    );

    // Defaults are left out: a preview names no `write`, a probe no list.
    let answers = |answer: &str| vec![(200, serde_json::json!({ "$raw": answer }))];
    let (server, store) = connected(answers(
        r#"{"conversations":[],"next_cursor":null,"write":false}"#,
    ));
    store.sweep_records(50, Some(cursor), false).unwrap();
    assert_eq!(
        sent(&server),
        format!(r#"{{"limit":50,"cursor":"{cursor}"}}"#)
    );
    let none = r#"{"restore":"r1","state":"none","position":null,"stored_bytes":0,"freed_bytes":0,"digest":null}"#;
    let (server, store) = connected(answers(none));
    let probe = Restore {
        restore: "r1".into(),
        ..Restore::default()
    };
    assert_eq!(
        store.restore_records("c", &probe).unwrap().state,
        Staged::None
    );
    assert_eq!(sent(&server), r#"{"conversation_id":"c","restore":"r1"}"#);
    let none = r#"{"draft":"d1","state":"none","next_seq":0,"turn":null,"stored_bytes":0,"freed_bytes":0}"#;
    let (server, store) = connected(answers(none));
    let probe = Write {
        draft: "d1".into(),
        ..Write::default()
    };
    assert_eq!(store.write_records("c", &probe).unwrap().state, State::None);
    assert_eq!(
        sent(&server),
        r#"{"conversation_id":"c","draft":"d1","records":[]}"#
    );
}
