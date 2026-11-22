//! A draft admits only what a read rebuilds, spelled as the codec writes it
//! (§40.54 item 9 "Checks", corrected after the review of 2481787): a
//! number exactly when the read's own parser takes it and in the codec's
//! spelling, string escapes the codec's alone, no key twice in an object
//! within the stated bound, no empty extra or null reasoning, client sizes
//! summed with checks, and each piece nested as deep as a format-1 row
//! holds it. The property: a turn a seal makes visible always rebuilds,
//! its JSON pieces exactly as the codec writes the rebuilt messages.

use serde_json::{Value, json};

use super::generate::{self, Rng};
use super::message;
use crate::conversation::Turn;
use crate::conversation::message::Role;
use crate::conversation::rows::{parse_turn, render_turn};
use crate::conversation::turn_records::{
    DraftCheck, Form, Kind, Refusal, Shape, TurnRecord, Write, messages_of, records_of,
};
use crate::store::time::Stamp;

const MAX: u64 = 1 << 30;

fn piece(kind: Kind, text: &str) -> TurnRecord {
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
fn checked(records: &[TurnRecord]) -> Result<(), String> {
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

/// Review finding 3: a wrapped sum passed both the stated total and the
/// store's bound in a release build and panicked in a debug one. Run with
/// overflow checks off too (`CARGO_PROFILE_TEST_OVERFLOW_CHECKS=false`).
#[test]
fn client_sizes_and_seqs_are_summed_with_checks() {
    let mut first = piece(Kind::Prompt, "x");
    (first.role, first.content) = (Role::User, Some(Form::Text));
    let second = TurnRecord {
        seq: 1,
        message: 1,
        role: Role::Tool,
        kind: Kind::ToolResult,
        content: Some(Form::Text),
        size: u64::MAX,
        text: "a".repeat(65_000),
        ..first.clone()
    };
    let mut check = DraftCheck::default();
    check.admit(&first, Some(100), 65_536).unwrap();
    let before = check.clone();
    match check.admit(&second, Some(100), 65_536) {
        Err(Refusal::Invalid(why)) => assert!(why.contains("overflows"), "{why}"),
        other => panic!("a wrapping size: {other:?}"),
    }
    assert_eq!(check, before, "a refused record changes nothing");
    let mut wrapped = first.clone();
    wrapped.seq = u64::MAX;
    let write = Write {
        draft: "d".into(),
        records: vec![wrapped, first],
        ..Write::default()
    };
    assert!(write.check().unwrap_err().contains("consecutive seq"));
}

/// A nested value `depth` containers deep: an object outside, arrays in it.
fn nested(depth: usize) -> Value {
    let mut value = json!(1);
    for _ in 1..depth {
        value = Value::Array(vec![value]);
    }
    json!({ "a": value })
}

/// Review finding 13: each piece nests as deep as a format-1 row can hold
/// it, so every stored turn converts and every record turn folds back. A
/// row is read with serde_json's limit; the levels around a piece there
/// leave 122 for a call's arguments or extra, 123 for an attachment and
/// 124 for a reasoning or a message's extra.
#[test]
fn each_piece_nests_as_deep_as_a_format_1_row_holds_it() {
    let messages = |depth: usize| -> Vec<(Kind, Value)> {
        let deep = nested(depth);
        let block = json!({"type": "compaction", "content": nested(depth - 1)});
        let call = |arguments: Value, extra: Value| {
            json!({"role": "assistant", "content": null,
                   "tool_calls": [{"id": "c", "name": "n", "arguments": arguments, "extra": extra}]})
        };
        vec![
            (Kind::ToolCall, call(deep.clone(), json!(null))),
            (Kind::Extra, call(json!({}), deep.clone())),
            (
                Kind::Attachment,
                json!({"role": "user", "content": [block]}),
            ),
            (
                Kind::Reasoning,
                json!({"role": "assistant", "content": "r", "reasoning": deep.clone()}),
            ),
            (
                Kind::Extra,
                json!({"role": "assistant", "content": "e", "extra": deep}),
            ),
        ]
    };
    let bounds = [122, 122, 123, 124, 124];
    for (at, bound) in bounds.into_iter().enumerate() {
        for depth in [bound, bound + 1] {
            let (kind, value) = messages(depth).swap_remove(at);
            let decoded = vec![message(&value)];
            let row = render_turn(&Turn {
                conversation_id: "c-1".into(),
                turn: 1,
                messages: vec![message(&value)],
                created_at: Stamp::now(),
                actor: None,
                redacted: false,
            });
            let stored = parse_turn(row.trim_end(), 1, "c-1");
            let records = records_of(&decoded).unwrap();
            let deepest = records
                .iter()
                .rfind(|r| r.kind == kind)
                .expect("the deep piece");
            let admitted = checked(&records);
            if depth == bound {
                assert!(stored.is_ok(), "{kind:?} at {depth}: the row reads");
                assert_eq!(admitted, Ok(()), "{kind:?} at {depth}: {}", deepest.seq);
            } else {
                assert!(stored.is_err(), "{kind:?} at {depth}: the row is refused");
                let refusal = admitted.expect_err("deeper than the row holds");
                assert!(
                    refusal.contains(&format!("nested deeper than {bound}")),
                    "{kind:?}: {refusal}"
                );
            }
        }
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
