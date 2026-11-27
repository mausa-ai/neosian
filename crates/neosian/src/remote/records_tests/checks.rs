//! A record page is checked against its request before a door sees it
//! (the T1fe rulings J2 and J4): one test per check, the answers a
//! conforming server sends accepted, a run of pages that does not move on
//! refused, and a read past its page budget refused.

use serde_json::{Value, json};

use super::{connected, unreadable};
use crate::conversation::turn_records::{Kind, RecordRead};
use crate::remote::records::{Failure, RecordPage};

fn header(turn: u64, redacted: bool) -> Value {
    json!({"turn":turn,"created_at":"2026-11-21T14:03:07Z","actor":null,"redacted":redacted,"records":9,"bytes":9})
}

/// A record of `kind` at `turn` and `seq`, chunk 0, holding `text` whole.
fn record(turn: u64, seq: u64, kind: &str, text: &str) -> Value {
    let role = match kind {
        "prompt" => "user",
        "tool_result" => "tool",
        _ => "assistant",
    };
    json!({"turn":turn,"seq":seq,"message":seq,"role":role,"kind":kind,"content":null,"block":null,"call":null,
           "call_id":null,"name":null,"chunk":0,"size":text.len(),"text":text,"tail":null,"truncated":false})
}

/// `record` answered as the head of a piece of `size` bytes, with `tail`.
fn headed(mut record: Value, size: u64, tail: &str) -> Value {
    record["size"] = json!(size);
    record["tail"] = json!(tail);
    record["truncated"] = json!(true);
    record
}

fn page(turns: Vec<Value>, records: Vec<Value>, continuation: Option<&str>) -> Value {
    json!({"turns":turns,"records":records,"continuation":continuation})
}

/// What a read of `pages` answered to `read` comes to: the pages visited
/// and what was refused.
fn read(read: &RecordRead, pages: Vec<Value>) -> (usize, Option<String>) {
    let answers = pages.into_iter().map(|page| (200, page)).collect();
    let (_server, store) = connected(answers);
    let mut visited = 0;
    let mut visit = |_: RecordPage| {
        visited += 1;
        Ok(true)
    };
    let ended = store.read_records("c", read, &mut visit);
    (visited, ended.is_err().then(|| unreadable(ended)))
}

fn refused(asked: &RecordRead, records: Vec<Value>, turns: Vec<Value>) -> String {
    let (visited, refused) = read(asked, vec![page(turns, records, None)]);
    assert_eq!(visited, 0, "a refused page is never handed on");
    refused.expect("a refusal")
}

/// Recall's light read of turn 1.
fn light() -> RecordRead {
    let kinds = [Kind::Prompt, Kind::Reply, Kind::ToolCall, Kind::ToolResult];
    RecordRead {
        turn: Some(1),
        kinds: Some(kinds.to_vec()),
        heads: vec![(Kind::ToolResult, 8), (Kind::Attachment, 1)],
        ..RecordRead::default()
    }
}

#[test]
fn a_record_outside_the_turns_asked_is_refused() {
    let one = vec![header(1, false)];
    let later = vec![record(2, 0, "prompt", "x")];
    let two = vec![header(1, false), header(2, false)];
    assert_eq!(
        refused(&light(), later, two.clone()),
        "a turn outside the turns asked"
    );
    // A record of turn 2 under turn 1's header alone.
    assert_eq!(
        refused(&light(), vec![record(2, 0, "prompt", "x")], one),
        "a record outside the turns asked"
    );
    let range = RecordRead {
        after: 1,
        through: Some(2),
        ..RecordRead::default()
    };
    let three = vec![header(3, false)];
    assert_eq!(
        refused(&range, Vec::new(), three),
        "a turn outside the turns asked"
    );
    assert_eq!(
        refused(&range, Vec::new(), vec![header(1, false)]),
        "a turn outside the turns asked"
    );
}

#[test]
fn a_record_needs_its_own_unredacted_header_on_the_page() {
    let range = RecordRead::default();
    assert_eq!(
        refused(
            &range,
            vec![record(2, 0, "prompt", "x")],
            vec![header(1, false)]
        ),
        "a record without its turn's header"
    );
    assert_eq!(
        refused(
            &range,
            vec![record(1, 0, "prompt", "x")],
            vec![header(1, true)]
        ),
        "a record of a redacted turn"
    );
}

#[test]
fn a_record_of_a_kind_not_asked_is_refused() {
    // Recall asked for turn 1 from seq 0: a reasoning record is not shown.
    let opaque = vec![record(1, 0, "reasoning", "{\"secret\":1}")];
    assert_eq!(
        refused(&light(), opaque, vec![header(1, false)]),
        "a record of a kind not asked"
    );
    let headers = RecordRead {
        kinds: Some(Vec::new()),
        ..RecordRead::default()
    };
    let prompt = vec![record(1, 0, "prompt", "x")];
    assert_eq!(
        refused(&headers, prompt, vec![header(1, false)]),
        "a record of a kind not asked"
    );
}

#[test]
fn a_record_before_the_seq_or_outside_the_piece_asked_is_refused() {
    let from = RecordRead {
        seq: Some(3),
        ..light()
    };
    assert_eq!(
        refused(
            &from,
            vec![record(1, 2, "prompt", "x")],
            vec![header(1, false)]
        ),
        "a record before the seq asked"
    );
    let piece = RecordRead {
        turn: Some(1),
        seq: Some(3),
        piece: true,
        ..RecordRead::default()
    };
    // Another piece than the one holding record 3.
    let other = vec![record(1, 4, "prompt", "x")];
    assert_eq!(
        refused(&piece, other, vec![header(1, false)]),
        "a record outside the piece asked"
    );
    let mut second = record(1, 5, "prompt", "y");
    second["chunk"] = json!(1);
    let two = vec![record(1, 3, "prompt", "x"), second];
    assert_eq!(
        refused(&piece, two, vec![header(1, false)]),
        "a record outside the piece asked"
    );
}

#[test]
fn a_head_longer_than_asked_is_refused() {
    let result = |text: &str, tail: &str| headed(record(1, 0, "tool_result", text), 100, tail);
    let one = || vec![header(1, false)];
    // Heads of 8 bytes, quotes included: six bytes of text at most.
    for (text, tail) in [("1234567", "x"), ("x", "1234567")] {
        let what = refused(&light(), vec![result(text, tail)], one());
        assert_eq!(what, "a head longer than asked");
    }
    // A piece over twice the head answered whole.
    assert_eq!(
        refused(
            &light(),
            vec![record(1, 0, "tool_result", "123456789012345")],
            one()
        ),
        "a piece longer than its head"
    );
    // A head of a kind the request reads whole, and a tail without a head.
    let mut prompt = result("x", "y");
    prompt["kind"] = json!("prompt");
    prompt["role"] = json!("user");
    assert_eq!(
        refused(&light(), vec![prompt], one()),
        "a head of a piece read whole"
    );
    let mut tail = record(1, 0, "tool_result", "x");
    tail["tail"] = json!("y");
    assert_eq!(
        refused(&light(), vec![tail], one()),
        "a tail without a head"
    );
}

#[test]
fn what_a_conforming_server_sends_is_accepted() {
    // A head and its tail within the bytes, a one-byte head holding no
    // text, a piece answered whole at twice the head, a redacted header
    // with no records, the header repeated on the next page.
    let head = headed(record(1, 0, "tool_result", "123456"), 100, "abcdef");
    let mut attachment = headed(record(1, 1, "prompt", ""), 5_000, "");
    attachment["kind"] = json!("attachment");
    attachment["block"] = json!(0);
    let mut asked = light();
    asked.kinds.as_mut().unwrap().push(Kind::Attachment);
    asked.turn = None;
    asked.through = Some(3);
    let pages = vec![
        page(vec![header(1, false)], vec![head, attachment], Some("a")),
        page(
            vec![header(1, false), header(2, true), header(3, false)],
            vec![
                record(1, 2, "tool_result", "12345678901234"),
                record(3, 0, "prompt", "x"),
            ],
            None,
        ),
    ];
    // The whole piece of 14 bytes is 16 encoded: twice the head of 8.
    assert_eq!(read(&asked, pages), (2, None));
    // A read that starts inside a piece answers its remaining chunks.
    let inside = RecordRead {
        seq: Some(4),
        ..light()
    };
    let mut rest = record(1, 4, "tool_result", "12345678901234567890");
    rest["chunk"] = json!(2);
    rest["size"] = json!(70_000);
    let pages = vec![page(vec![header(1, false)], vec![rest], None)];
    assert_eq!(read(&inside, pages), (1, None));
}

#[test]
fn a_run_of_pages_that_do_not_move_on_is_refused() {
    // One header and no records, under two continuations in turn.
    let repeat = |token: &str| page(vec![header(1, false)], Vec::new(), Some(token));
    let pages = vec![
        page(
            vec![header(1, false)],
            vec![record(1, 0, "prompt", "x")],
            Some("a"),
        ),
        repeat("b"),
        repeat("a"),
    ];
    let still = Some("a page that does not move on".to_string());
    assert_eq!(read(&RecordRead::default(), pages), (2, still));
}

#[test]
fn a_read_asks_for_at_most_its_page_budget() {
    let pages: Vec<(u16, Value)> = (1..=3)
        .map(|turn| {
            let token = format!("p{turn}");
            (
                200,
                page(vec![header(turn, false)], Vec::new(), Some(&token)),
            )
        })
        .collect();
    let (server, store) = connected(pages);
    let mut visited = 0;
    let mut visit = |_: RecordPage| {
        visited += 1;
        Ok(true)
    };
    let ended = store.read_pages("c", &RecordRead::default(), 3, &mut visit);
    assert!(
        matches!(ended, Err(Failure::Unreadable(ref what)) if what == "a read that goes on past 3 pages")
    );
    assert_eq!(visited, 3);
    assert_eq!(server.requests.lock().unwrap().len(), 4);
}
