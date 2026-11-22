//! What the records refuse: a message they cannot carry, a record outside
//! its bounds or its shape, a record set out of order; and the largest
//! record the bounds allow, as written and as answered.

use serde_json::json;

use super::message;
use crate::conversation::message::Role;
use crate::conversation::turn_records::{
    Form, ID_BYTES, Kind, RECORD_BYTES, TEXT_BYTES, TurnRecord, each_record, messages_of,
    records_of,
};

/// A reply, a call with an extra, and reasoning: five records.
fn sample() -> Vec<TurnRecord> {
    records_of(&[message(&json!({
        "role": "assistant", "content": "ok",
        "tool_calls": [{"id": "c", "name": "n", "arguments": {"a": 1}, "extra": {"e": 1}}],
        "reasoning": "r", "extra": {"x": 1}
    }))])
    .unwrap()
}

/// A change made to `sample()`'s records.
type Change = Box<dyn FnOnce(&mut Vec<TurnRecord>)>;

fn refused(change: impl FnOnce(&mut Vec<TurnRecord>)) -> String {
    let mut records = sample();
    change(&mut records);
    messages_of(&records).expect_err("refused")
}

#[test]
fn an_id_or_a_name_past_the_bound_refuses_the_turn() {
    let long = "i".repeat(ID_BYTES + 1);
    for sent in [
        json!({"role": "tool", "content": "x", "tool_call_id": long}),
        json!({"role": "assistant", "tool_calls": [{"id": long, "name": "n"}]}),
        json!({"role": "assistant", "tool_calls": [{"id": "c", "name": long}]}),
    ] {
        let why = records_of(&[message(&sent)]).expect_err("refused");
        assert!(why.contains("longer than 1024 bytes"), "{why}");
    }
    let at_bound = json!({"role": "tool", "content": "x", "tool_call_id": "é".repeat(512)});
    assert!(records_of(&[message(&at_bound)]).is_ok());
    let turn = [
        message(&json!({"role": "user", "content": "q"})),
        message(&json!({
            "role": "assistant", "tool_calls": [{"id": long, "name": "n"}]
        })),
    ];
    let mut visited = 0;
    assert!(each_record(&turn, &mut |_| visited += 1).is_err());
    assert_eq!(visited, 0, "a refused turn visits no record");
}

#[test]
fn a_record_set_out_of_order_or_shape_is_refused_where_it_breaks() {
    let cases: Vec<(&str, Change)> = vec![
        ("at least one record", Box::new(|r| r.clear())),
        ("stands where record 1 belongs", Box::new(|r| r[1].seq = 7)),
        (
            "message 1 where message 0 is next",
            Box::new(|r| r.iter_mut().for_each(|r| r.message = 1)),
        ),
        (
            "carries its content form",
            Box::new(|r| r[0].content = None),
        ),
        (
            "only a message's first record carries content",
            Box::new(|r| r[2].content = Some(Form::Text)),
        ),
        ("chunk 1 continues no piece", Box::new(|r| r[1].chunk = 1)),
        (
            "the piece before it is incomplete",
            Box::new(|r| r[0].size = 4),
        ),
        (
            "not the next chunk of its piece",
            Box::new(|r| {
                r[0].size = 4;
                r[1].chunk = 1;
            }),
        ),
        ("ends inside a piece", Box::new(|r| r[4].size = 99)),
        ("does not fit size", Box::new(|r| r[0].size = 1)),
        ("piece out of place", Box::new(|r| r.swap(3, 4))),
        (
            "not JSON",
            Box::new(|r| {
                r[1].text = "{\"a\":".into();
                r[1].size = 5;
            }),
        ),
        (
            "arguments must be an object",
            Box::new(|r| {
                r[1].text = "[1]".into();
                r[1].size = 3;
            }),
        ),
        (
            "call_id does not match",
            Box::new(|r| r[3].call_id = Some("c".into())),
        ),
        (
            "a tool_call record of this shape cannot belong",
            Box::new(|r| r[1].name = None),
        ),
        (
            "a prompt record of this shape cannot belong to a message of role assistant",
            Box::new(|r| r[0].kind = Kind::Prompt),
        ),
        (
            "a message has one role",
            Box::new(|r| r[4].role = Role::User),
        ),
    ];
    for (expected, change) in cases {
        let why = refused(|r| {
            change(r);
            for (at, record) in r
                .iter_mut()
                .enumerate()
                .filter(|_| expected != "stands where record 1 belongs")
            {
                record.seq = at as u64;
            }
        });
        assert!(why.contains(expected), "{expected}: {why}");
    }
}

#[test]
fn an_attachment_holds_a_block_other_than_text() {
    let mut records = records_of(&[message(
        &json!({"role": "user", "content": [{"type": "image", "url": "u"}]}),
    )])
    .unwrap();
    for (text, expected) in [
        (r#"{"type":"text","text":"t"}"#, "a block other than text"),
        (r#"{"type":"video"}"#, "Unknown content block type"),
    ] {
        records[0].text = text.into();
        records[0].size = text.len() as u64;
        let why = messages_of(&records).expect_err("refused");
        assert!(why.contains(expected), "{why}");
    }
}

#[test]
fn a_record_from_json_is_read_strictly() {
    let good = sample()[0].to_json(Some(1));
    let with = |key: &str, value: serde_json::Value| {
        let mut changed = good.clone();
        changed[key] = value;
        TurnRecord::from_json(&changed).expect_err("refused")
    };
    assert!(with("tail", json!(null)).contains("unknown record field \"tail\""));
    assert!(with("turn", json!(0)).contains(">= 1"));
    assert!(with("turn", json!(null)).contains(">= 1"));
    assert!(with("seq", json!(-1)).contains("non-negative integer"));
    assert!(with("seq", json!(1.0)).contains("non-negative integer"));
    assert!(with("kind", json!("tool")).contains("unknown record kind"));
    assert!(with("role", json!("robot")).contains("unknown record role"));
    assert!(with("content", json!("html")).contains("unknown content form"));
    assert!(with("call_id", json!(5)).contains("must be a string"));
    let mut bare = good.clone();
    bare.as_object_mut()
        .unwrap()
        .retain(|_, value| !value.is_null());
    assert_eq!(
        TurnRecord::from_json(&bare).unwrap().1,
        sample()[0],
        "null keys may be omitted"
    );
    bare.as_object_mut().unwrap().remove("text");
    assert!(
        TurnRecord::from_json(&bare)
            .unwrap_err()
            .contains("\"text\" is required")
    );
}

#[test]
fn the_largest_record_fits_its_bound_as_written_and_as_answered() {
    let id = "\u{1}".repeat(ID_BYTES);
    let record = TurnRecord {
        seq: u64::MAX,
        message: u64::MAX,
        role: Role::Assistant,
        kind: Kind::ToolResult,
        content: Some(Form::Blocks),
        block: Some(u64::MAX),
        call: Some(u64::MAX),
        call_id: Some(id.clone()),
        name: Some(id),
        chunk: u64::MAX,
        size: u64::MAX,
        text: "\u{1}".repeat((TEXT_BYTES - 2) / 6),
    };
    let written = record.canonical(Some(u64::MAX));
    let answered = format!(
        "{},\"tail\":null,\"truncated\":false}}",
        &written[..written.len() - 1]
    );
    assert!(answered.len() <= RECORD_BYTES, "{}", answered.len());
    let half = TurnRecord {
        text: "\u{1}".repeat((TEXT_BYTES / 2 - 2) / 6),
        ..record
    };
    let headed = half.canonical(Some(u64::MAX));
    let tail = crate::pyfmt::dumps(&json!(half.text), false);
    let answered = format!(
        "{},\"tail\":{tail},\"truncated\":true}}",
        &headed[..headed.len() - 1]
    );
    assert!(answered.len() <= RECORD_BYTES, "{}", answered.len());
}
