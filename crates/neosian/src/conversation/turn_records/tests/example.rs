//! Item 3.8's turn and the worked numbers of items 9 and 12, with every
//! expected value written by hand from the design. The tool result is 812
//! lines of 1,000 bytes and a last line of 345, so the split rule cuts it
//! into twelve chunks of 65,000 bytes and one of 32,345, the chunk length
//! items 3.8 and 9 work with.

use serde_json::json;

use super::message;
use crate::conversation::message::Message;
use crate::conversation::turn_records::{Header, Sizes, TurnRecord, UnitDigest, records_of};
use crate::conversation::{Projection, Redaction};
use crate::pyfmt::dumps;
use crate::store::time::Stamp;

const STAMPED: &str = "client:default/claude-code:7f3a";

fn line() -> String {
    format!("{}\n", "a".repeat(999))
}

fn example() -> Vec<Message> {
    let result = format!("{}{}", line().repeat(812), "a".repeat(345));
    [
        json!({"role": "user", "content": "run the tests"}),
        json!({"role": "assistant", "content": null,
               "tool_calls": [{"id": "toolu_01", "name": "Bash", "arguments": {"command": "make test"}}]}),
        json!({"role": "tool", "content": result, "tool_call_id": "toolu_01"}),
        json!({"role": "assistant", "content": "All 412 tests pass."}),
    ]
    .iter()
    .map(message)
    .collect()
}

fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).expect("a stamp")
}

/// A record's canonical line without its text.
fn place(record: &TurnRecord) -> String {
    let mut value = record.to_json(None);
    value.as_object_mut().unwrap().remove("text");
    dumps(&value, false)
}

#[test]
fn the_example_turn_becomes_the_records_of_the_example_table() {
    let records = records_of(&example()).unwrap();
    let mut expected = vec![
        r#"{"seq":0,"message":0,"role":"user","kind":"prompt","content":"text","block":null,"call":null,"call_id":null,"name":null,"chunk":0,"size":13}"#.to_string(),
        r#"{"seq":1,"message":1,"role":"assistant","kind":"tool_call","content":"none","block":null,"call":0,"call_id":"toolu_01","name":"Bash","chunk":0,"size":23}"#.to_string(),
    ];
    for chunk in 0..13 {
        let content = if chunk == 0 { r#""text""# } else { "null" };
        expected.push(format!(
            r#"{{"seq":{},"message":2,"role":"tool","kind":"tool_result","content":{content},"block":null,"call":null,"call_id":"toolu_01","name":null,"chunk":{chunk},"size":812345}}"#,
            chunk + 2
        ));
    }
    expected.push(r#"{"seq":15,"message":3,"role":"assistant","kind":"reply","content":"text","block":null,"call":null,"call_id":null,"name":null,"chunk":0,"size":19}"#.to_string());
    assert_eq!(records.iter().map(place).collect::<Vec<_>>(), expected);
    let mut texts = vec![
        "run the tests".to_string(),
        r#"{"command":"make test"}"#.to_string(),
    ];
    texts.extend((0..12).map(|_| line().repeat(65)));
    texts.push(format!("{}{}", line().repeat(32), "a".repeat(345)));
    texts.push("All 412 tests pass.".to_string());
    assert_eq!(
        records.iter().map(|r| r.text.clone()).collect::<Vec<_>>(),
        texts
    );
    let header = Header::of(
        42,
        stamp("2026-11-21T14:03:07.512000Z"),
        Some(STAMPED.into()),
        &records,
    );
    assert_eq!(
        header.canonical(),
        r#"{"turn":42,"created_at":"2026-11-21T14:03:07.512000Z","actor":"client:default/claude-code:7f3a","redacted":false,"records":16,"bytes":812400}"#
    );
    let digest: String = records[1]
        .digest(None)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    assert_eq!(
        digest,
        "9b0e749f08d72229ad910018bc8cb75d71ad5283ffa17205b7a97ebc6b97cc3b"
    );
}

#[test]
fn the_write_example_stores_the_bytes_item_9_states() {
    let records = records_of(&example()).unwrap();
    let sizes = Sizes::new("cc-7f3a", "d7c1e0b2a9f44e57");
    let each: Vec<u64> = records.iter().map(|record| sizes.record(record)).collect();
    assert_eq!(&each[..3], [283, 305, 65_278]);
    assert_eq!((each[14], each[15]), (32_623, 289));
    assert_eq!(
        (sizes.bookkeeping(), sizes.header(Some(STAMPED))),
        (1_054, 587)
    );
    assert_eq!(sizes.bookkeeping() + each[..9].iter().sum::<u64>(), 458_588);
    assert_eq!(
        each[9..].iter().sum::<u64>() + sizes.header(Some(STAMPED)),
        359_889
    );
}

#[test]
fn the_restore_example_stores_the_bytes_and_the_digest_item_12_states() {
    let actor = Some("claude-code:7f3a");
    let sizes = Sizes::new("cc-7f3a", "iecf9f710d465bc0ea0fe2fbba8915596");
    let first = records_of(&[
        message(&json!({"role": "system", "content": "x".repeat(32)})),
        message(&json!({"role": "user", "content": "y".repeat(500)})),
        message(&json!({"role": "assistant", "content": "z".repeat(1_000)})),
    ])
    .unwrap();
    let third =
        records_of(&[message(&json!({"role": "user", "content": "w".repeat(44)}))]).unwrap();
    let one = Header::of(
        1,
        stamp("2026-10-02T09:15:00.000001Z"),
        actor.map(String::from),
        &first,
    );
    let two = Header {
        turn: 2,
        created_at: stamp("2026-10-02T09:20:11Z"),
        actor: actor.map(String::from),
        redacted: true,
        records: 0,
        bytes: 0,
    };
    let three = Header::of(
        3,
        stamp("2026-10-02T09:31:40Z"),
        actor.map(String::from),
        &third,
    );
    let entry = Projection {
        turn: 3,
        kind: "epoch".into(),
        text: "set up the cache".into(),
        span: 3,
    };
    let act = Redaction {
        conversation_id: "cc-7f3a".into(),
        turns: vec![2],
        actor: Some("client:default/ops".into()),
        created_at: stamp("2026-10-05T08:00:00Z"),
    };
    let terms = [
        sizes.header(actor),
        sizes.bookkeeping(),
        sizes.redacted_header(actor),
        sizes.record(&third[0]),
        sizes.projection(&entry.text),
        sizes.act(act.actor.as_deref(), act.turns.len()),
        sizes.conversation_row(),
        sizes.committed_marker(),
    ];
    assert_eq!(terms, [589, 1_071, 286, 314, 158, 164, 263, 303]);
    let records: u64 = first.iter().map(|record| sizes.record(record)).sum();
    assert_eq!(records, 2_342);
    assert_eq!(sizes.header(actor) + records + sizes.bookkeeping(), 4_002);
    assert_eq!(terms[2..].iter().sum::<u64>() + sizes.header(actor), 2_077);
    assert_eq!(
        (one.canonical(), two.canonical(), three.canonical()),
        (
            r#"{"turn":1,"created_at":"2026-10-02T09:15:00.000001Z","actor":"claude-code:7f3a","redacted":false,"records":3,"bytes":1532}"#.to_string(),
            r#"{"turn":2,"created_at":"2026-10-02T09:20:11Z","actor":"claude-code:7f3a","redacted":true,"records":0,"bytes":0}"#.to_string(),
            r#"{"turn":3,"created_at":"2026-10-02T09:31:40Z","actor":"claude-code:7f3a","redacted":false,"records":1,"bytes":44}"#.to_string(),
        )
    );
    let mut unit = UnitDigest::default();
    unit.header(&one);
    first.iter().for_each(|record| unit.record(1, record));
    unit.header(&two);
    unit.header(&three);
    unit.record(3, &third[0]);
    unit.projection(&entry);
    unit.act(&act);
    assert_eq!(
        unit.finish(),
        "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32"
    );
}
