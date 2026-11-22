//! A draft admits only what a read rebuilds, spelled as the codec writes it
//! (§40.54 item 9 "Checks", corrected after the review of 2481787): a
//! number exactly when the read's own parser takes it and in the codec's
//! spelling, string escapes the codec's alone, no key twice in an object
//! within the stated bound, no empty extra or null reasoning. The property:
//! a turn a seal makes visible always rebuilds, its JSON pieces exactly as
//! the codec writes the rebuilt messages. The bounds are in `bounds.rs`.

use serde_json::Value;

use super::generate::{self, Rng};
use super::message;
use crate::conversation::message::Role;
use crate::conversation::turn_records::{
    DraftCheck, Form, Kind, Shape, TurnRecord, messages_of, records_of,
};

const MAX: u64 = 1 << 30;

pub(super) fn piece(kind: Kind, text: &str) -> TurnRecord {
    let (role, call, call_id, name) = match kind {
        Kind::ToolCall => (Role::Assistant, Some(0), Some("c".into()), Some("n".into())),
        _ => (Role::Assistant, None, None, None),
    };
    TurnRecord {
        seq: 0,
        message: 0,
        role,
        kind,
        content: Some(Form::None),
        block: None,
        call,
        call_id,
        name,
        chunk: 0,
        size: text.len() as u64,
        text: text.into(),
    }
}

/// Every record through one check, its state kept as a store keeps it.
pub(super) fn checked(records: &[TurnRecord]) -> Result<(), String> {
    let mut check = DraftCheck::default();
    for record in records {
        let kept: Value = serde_json::to_value(&check).unwrap();
        check = serde_json::from_value(kept).unwrap();
        check
            .admit(record, None, MAX)
            .map_err(|refusal| format!("{refusal:?}"))?;
    }
    check.sealable()
}

/// Each JSON piece's text, its chunks joined, in order.
fn json_pieces(records: &[TurnRecord]) -> Vec<(Kind, String)> {
    let mut pieces: Vec<(Kind, String)> = Vec::new();
    for record in records.iter().filter(|r| Shape::of(r.kind).is_some()) {
        match pieces.last_mut() {
            Some((_, text)) if record.chunk > 0 => text.push_str(&record.text),
            _ => pieces.push((record.kind, record.text.clone())),
        }
    }
    pieces
}

/// Admitted, a piece rebuilds and converts back to the very same text.
fn admitted_as_written(kind: Kind, text: &str) -> Result<(), String> {
    let records = vec![piece(kind, text)];
    checked(&records)?;
    let rebuilt = messages_of(&records).map_err(|why| format!("unreadable: {why}"))?;
    let again = records_of(&rebuilt).unwrap();
    assert_eq!(json_pieces(&again), json_pieces(&records), "{text}");
    Ok(())
}

fn refused(kind: Kind, text: &str) -> String {
    checked(&[piece(kind, text)]).expect_err(text)
}

#[test]
fn numbers_are_admitted_as_the_read_parses_them_and_the_codec_spells_them() {
    for number in [
        "0",
        "-1",
        "100",
        "1.0",
        "1.5",
        "-0.0",
        "0.0001",
        "1e-05",
        "1000000000000000.0",
        "1e+16",
        "1.7976931348623157e+308",
        "-1.7976931348623157e+308",
        "2.2250738585072014e-308",
        "5e-324",
        "9223372036854775807",
        "-9223372036854775808",
        "18446744073709551615",
        "1.8446744073709552e+19",
    ] {
        admitted_as_written(Kind::ToolCall, &format!("{{\"a\":{number}}}"))
            .unwrap_or_else(|why| panic!("{number}: {why}"));
    }
    for (number, why) in [
        ("1.7976931348623158e308", "out of range"),
        ("1e400", "out of range"),
        (
            "1.7976931348623157e308",
            "not spelled as the codec writes it",
        ),
        ("-0", "not spelled"),
        ("1.50", "not spelled"),
        ("1E2", "not spelled"),
        ("1e2", "not spelled"),
        ("1e16", "not spelled"),
        ("1e-5", "not spelled"),
        ("0.10", "not spelled"),
        ("18446744073709551616", "not spelled"),
        ("-9223372036854775809", "not spelled"),
        ("17976931348623157e292", "not spelled"),
    ] {
        let refusal = refused(Kind::ToolCall, &format!("{{\"a\":{number}}}"));
        assert!(refusal.contains(why), "{number}: {refusal}");
    }
}

#[test]
fn strings_are_admitted_only_in_the_codecs_escapes() {
    for text in [
        r#""a\"b\\c""#,
        r#""\b\f\n\r\t""#,
        r#""\u0000\u0001\u000b\u001f""#,
        "\"é中😀\u{7f}\u{2028}/\"",
    ] {
        admitted_as_written(Kind::Reasoning, text).unwrap_or_else(|why| panic!("{text}: {why}"));
    }
    for text in [
        r#""\/""#,
        r#""\u0041""#,
        r#""\u000a""#,
        r#""\u0008""#,
        r#""\u001F""#,
        r#""\u007f""#,
        r#""\u00e9""#,
        r#""\ud83d\ude00""#,
    ] {
        let refusal = refused(Kind::Reasoning, text);
        assert!(
            refusal.contains("an escape the codec does not write"),
            "{text}: {refusal}"
        );
    }
}

/// Keys are compared while those held for the open objects stay within
/// 4,096 bytes, a key costing its encoded length and one; past that an
/// object's later keys are stored as written (§40.54 says what a reader
/// then gets).
#[test]
fn a_key_repeated_in_its_object_is_refused_within_the_stated_bound() {
    for text in [
        r#"{"a":1,"a":2}"#,
        r#"{"x":{"a":1,"b":2,"a":3}}"#,
        r#"{"":1,"":2}"#,
        r#"{"a\"":1,"a\"":2}"#,
    ] {
        let refusal = refused(Kind::ToolCall, text);
        assert!(refusal.contains("a key repeated"), "{text}: {refusal}");
    }
    for text in [
        r#"{"a":{"a":1}}"#,
        r#"{"a":[{"a":1},{"a":1}]}"#,
        r#"{"a":1,"b":{"c":1},"c":2}"#,
    ] {
        admitted_as_written(Kind::ToolCall, text).unwrap_or_else(|why| panic!("{text}: {why}"));
    }
    let keys = |count: usize| -> String {
        (0..count)
            .map(|at| format!("\"k{at:04}\":0"))
            .collect::<Vec<_>>()
            .join(",")
    };
    // A five-byte key costs six: 681 keys and the repeat cost 4,092 bytes,
    // within 4,096; after 682 keys the repeat would pass them.
    let within = format!("{{{},\"k0000\":1}}", keys(681));
    assert!(refused(Kind::ToolCall, &within).contains("a key repeated"));
    let past = format!("{{{},\"k0000\":1}}", keys(682));
    assert_eq!(checked(&[piece(Kind::ToolCall, &past)]), Ok(()));
}

#[test]
fn an_empty_extra_or_a_null_reasoning_is_refused() {
    for text in ["null", "false", "0", "0.0", "-0.0", "\"\"", "[]", "{}"] {
        let refusal = refused(Kind::Extra, text);
        assert!(
            refusal.contains("the codec leaves an empty extra out"),
            "{text}: {refusal}"
        );
    }
    for text in ["{\"x\":1}", "true", "1", "\"s\"", "[0]"] {
        admitted_as_written(Kind::Extra, text).unwrap_or_else(|why| panic!("{text}: {why}"));
    }
    let refusal = refused(Kind::Reasoning, "null");
    assert!(
        refusal.contains("the codec leaves a null reasoning out"),
        "{refusal}"
    );
    for text in ["false", "\"\"", "[]", "{}", "0"] {
        admitted_as_written(Kind::Reasoning, text).unwrap_or_else(|why| panic!("{text}: {why}"));
    }
}

/// Spellings at the limits and next to them: the codec's, and others the
/// read takes or refuses.
const SPELLINGS: &[&str] = &[
    "0",
    "-0.0",
    "1e+16",
    "1e-05",
    "1.7976931348623157e+308",
    "-1.7976931348623157e+308",
    "2.2250738585072014e-308",
    "5e-324",
    "18446744073709551615",
    "-9223372036854775808",
    "1.8446744073709552e+19",
    "\"\\u001f\\n\"",
    "\"\u{7f}é\"",
    "{\"k\":1,\"j\":[]}",
    "1.7976931348623158e308",
    "1.7976931348623157e308",
    "1e400",
    "-0",
    "1.50",
    "1E2",
    "1e16",
    "18446744073709551616",
    "-9223372036854775809",
    "\"\\/\"",
    "\"\\u0041\"",
    "\"\\u001F\"",
    "\"\\ud83d\\ude00\"",
    "{\"k\":1,\"k\":2}",
    "null",
    "[]",
    "{}",
];

/// A one-chunk JSON piece respelled with a value from `SPELLINGS`.
fn respell(records: &mut [TurnRecord], rng: &mut Rng) {
    let candidates: Vec<usize> = (0..records.len())
        .filter(|at| {
            let record = &records[*at];
            Shape::of(record.kind).is_some() && record.chunk == 0 && record.size < 60_000
        })
        .filter(|at| records[*at].size == records[*at].text.len() as u64)
        .collect();
    let Some(&at) = candidates.get(rng.below(candidates.len() as u64 + 1) as usize) else {
        return;
    };
    let value = SPELLINGS[rng.below(SPELLINGS.len() as u64) as usize];
    let record = &mut records[at];
    record.text = match record.kind {
        Kind::ToolCall => format!("{{\"a\":{value}}}"),
        Kind::Attachment => format!("{{\"type\":\"compaction\",\"content\":{value}}}"),
        _ => value.to_string(),
    };
    record.size = record.text.len() as u64;
}

#[test]
fn a_turn_a_seal_makes_visible_always_rebuilds_as_the_codec_writes_it() {
    let (mut admitted, mut refused) = (0, 0);
    for seed in 0..6_000 {
        let mut rng = Rng::new(70_000 + seed);
        let messages: Vec<_> = (0..=rng.below(3))
            .map(|_| message(&generate::message(&mut rng, false)))
            .collect();
        let mut records = records_of(&messages).unwrap();
        respell(&mut records, &mut rng);
        if checked(&records).is_err() {
            refused += 1;
            continue;
        }
        admitted += 1;
        let rebuilt = messages_of(&records)
            .unwrap_or_else(|why| panic!("seed {seed}: admitted but unreadable: {why}"));
        let again = records_of(&rebuilt).unwrap();
        assert_eq!(
            json_pieces(&again),
            json_pieces(&records),
            "seed {seed}: admitted but not as the codec writes it"
        );
    }
    assert!(admitted > 2_000 && refused > 500, "{admitted} {refused}");
}
