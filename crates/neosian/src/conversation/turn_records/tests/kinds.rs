//! One literal turn per kind and the edge cases of §40.54 item 3.3 with
//! its implementation note, written by hand in `kinds.json`: each turn
//! converts to exactly those records, in canonical encoding, and those
//! records rebuild the turn byte for byte.

use std::collections::BTreeSet;

use serde_json::Value;

use super::{message, spelled};
use crate::conversation::message::Message;
use crate::conversation::turn_records::{Form, Kind, TurnRecord, messages_of, records_of};
use crate::pyfmt::dumps;

const CASES: &str = include_str!("kinds.json");

fn hex(digest: [u8; 32]) -> String {
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[test]
fn each_kind_converts_to_the_records_written_by_hand() {
    let cases: Value = serde_json::from_str(CASES).unwrap();
    let mut kinds = BTreeSet::new();
    for case in cases.as_array().unwrap() {
        let name = case["case"].as_str().unwrap();
        let messages: Vec<Message> = case["messages"]
            .as_array()
            .unwrap()
            .iter()
            .map(message)
            .collect();
        let literal = case["records"].as_array().unwrap();
        let expected: Vec<String> = literal.iter().map(|record| dumps(record, false)).collect();
        let records = records_of(&messages).unwrap();
        let written: Vec<String> = records
            .iter()
            .map(|record| record.canonical(None))
            .collect();
        assert_eq!(written, expected, "{name}");
        let read: Vec<TurnRecord> = literal
            .iter()
            .map(|record| TurnRecord::from_json(record).unwrap().1)
            .collect();
        kinds.extend(read.iter().map(|record| record.kind.as_str()));
        let rebuilt: Vec<String> = messages_of(&read).unwrap().iter().map(spelled).collect();
        let sent: Vec<String> = messages.iter().map(spelled).collect();
        assert_eq!(rebuilt, sent, "{name}");
    }
    let all: BTreeSet<&str> = Kind::ALL.iter().map(|kind| kind.as_str()).collect();
    assert_eq!(kinds, all, "every kind has a literal turn");
}

#[test]
fn kinds_and_forms_are_spelled_as_the_design_spells_them() {
    let names: Vec<&str> = Kind::ALL.iter().map(|kind| kind.as_str()).collect();
    let expected = [
        "prompt",
        "reply",
        "system",
        "tool_result",
        "tool_call",
        "reasoning",
        "attachment",
        "extra",
    ];
    assert_eq!(names, expected);
    let searchable: Vec<&str> = Kind::ALL
        .iter()
        .filter(|kind| kind.searchable())
        .map(|kind| kind.as_str())
        .collect();
    assert_eq!(
        searchable,
        ["prompt", "reply", "system", "tool_result", "tool_call"]
    );
    for kind in Kind::ALL {
        assert_eq!(Kind::parse(kind.as_str()), Some(kind));
    }
    for form in [Form::Text, Form::Blocks, Form::None] {
        assert_eq!(Form::parse(form.as_str()), Some(form));
    }
    assert_eq!((Kind::parse("tool"), Form::parse("null")), (None, None));
}

/// The digest was computed independently (Python's hashlib) over the
/// canonical line below, written out by hand.
#[test]
fn a_record_digest_is_sha256_over_its_canonical_line() {
    let cases: Value = serde_json::from_str(CASES).unwrap();
    let (_, record) = TurnRecord::from_json(&cases[0]["records"][0]).unwrap();
    let line = concat!(
        r#"{"seq":0,"message":0,"role":"user","kind":"prompt","content":"text","block":null,"#,
        r#""call":null,"call_id":null,"name":null,"chunk":0,"size":17,"text":"héllo \"you\"\n\ttab"}"#
    );
    assert_eq!(record.canonical(None), line);
    assert_eq!(
        hex(record.digest(None)),
        "0b3fe5a6d69321d550433d402bcda474107a78dc59c950409ecc11aebbaa556a"
    );
    let restore = record.canonical(Some(3));
    assert_eq!(restore, format!("{{\"turn\":3,{}", &line[1..]));
}
