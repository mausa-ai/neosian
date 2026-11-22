//! The bounds a draft check holds after the review of 2481787: client
//! sizes and seqs summed with checks (finding 3), and each piece nested as
//! deep as a format-1 row holds it, shown against the row parser itself
//! (finding 13).

use serde_json::{Value, json};

use super::canonical::{checked, piece};
use super::message;
use crate::conversation::Turn;
use crate::conversation::message::Role;
use crate::conversation::rows::{parse_turn, render_turn};
use crate::conversation::turn_records::{
    DraftCheck, Form, Kind, Refusal, TurnRecord, Write, records_of,
};
use crate::store::time::Stamp;

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
