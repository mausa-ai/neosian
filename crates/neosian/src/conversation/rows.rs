//! The turn log's row codec and layout names (`conversation/file_rows.py`,
//! DESIGN §9.8): one compact JSON object per line, `neosian_format` on
//! every row, ISO-Z stamps; a malformed or newer row is refused, never
//! skipped, since skipping would corrupt the numbering.

use serde_json::{Map, Value, json};

use super::message::{self, Message};
use super::{FORMAT, Projection, Refused, Turn};
use crate::pyfmt::dumps;
use crate::store::time::{Stamp, Unreadable};

pub const CONVERSATIONS: &str = "conversations";
pub const TURNS: &str = "turns.jsonl";
pub const PROJECTIONS: &str = "projections.jsonl";

fn parse_object(
    line: &str,
    place: &str,
    conversation_id: &str,
) -> Result<Map<String, Value>, Refused> {
    let refuse = |reason: String| Refused::format_unsupported(conversation_id, &reason);
    let data: Value =
        serde_json::from_str(line).map_err(|_| refuse(format!("malformed {place}")))?;
    let Value::Object(data) = data else {
        return Err(refuse(format!("{place} is not an object")));
    };
    let declared = data.get("neosian_format").and_then(Value::as_i64);
    let Some(declared) = declared.filter(|declared| *declared >= 1) else {
        return Err(refuse(format!("{place} missing format")));
    };
    if declared as u64 > FORMAT {
        return Err(refuse(format!(
            "{place} format {declared} is newer than supported"
        )));
    }
    Ok(data)
}

fn stamp(raw: Option<&Value>, place: &str, conversation_id: &str) -> Result<Stamp, Refused> {
    let refuse = |reason: String| Refused::format_unsupported(conversation_id, &reason);
    let Some(text) = raw.and_then(Value::as_str) else {
        return Err(refuse(format!("{place} has invalid fields")));
    };
    Stamp::parse(text).map_err(|why| {
        let kind = match why {
            Unreadable::Malformed => "an unreadable",
            Unreadable::Naive => "a naive",
        };
        refuse(format!("{place} has {kind} timestamp"))
    })
}

/// One turn as the Python side writes it (`render_turn`): compact,
/// ASCII-escaped, the keys in its order, `redacted` only when set.
pub fn render_turn(turn: &Turn) -> String {
    let mut data = Map::new();
    data.insert("neosian_format".to_string(), json!(FORMAT));
    data.insert("turn".to_string(), json!(turn.turn));
    data.insert("created_at".to_string(), json!(turn.created_at.z()));
    let messages = turn.messages.iter().map(message::to_json).collect();
    data.insert("messages".to_string(), Value::Array(messages));
    data.insert("actor".to_string(), json!(turn.actor));
    if turn.redacted {
        data.insert("redacted".to_string(), json!(true));
    }
    format!("{}\n", dumps(&Value::Object(data), true))
}

/// One turn row; a redacted row carries no messages and every other row
/// carries some.
pub fn parse_turn(line: &str, number: usize, conversation_id: &str) -> Result<Turn, Refused> {
    let place = format!("turn-log line {number}");
    let data = parse_object(line, &place, conversation_id)?;
    let refuse = |reason: String| Refused::format_unsupported(conversation_id, &reason);
    let turn = data
        .get("turn")
        .and_then(Value::as_u64)
        .filter(|turn| *turn >= 1);
    let encoded = data.get("messages").and_then(Value::as_array);
    let actor = match data.get("actor") {
        None | Some(Value::Null) => Some(None),
        Some(Value::String(actor)) => Some(Some(actor.clone())),
        Some(_) => None,
    };
    let redacted = match data.get("redacted") {
        None => Some(false),
        Some(Value::Bool(flag)) => Some(*flag),
        Some(_) => None,
    };
    let (Some(turn), Some(encoded), Some(actor), Some(redacted)) = (turn, encoded, actor, redacted)
    else {
        return Err(refuse(format!("{place} has invalid fields")));
    };
    if encoded.is_empty() != redacted {
        return Err(refuse(format!("{place} has invalid fields")));
    }
    let created_at = stamp(data.get("created_at"), &place, conversation_id)?;
    let messages: Vec<Message> = encoded
        .iter()
        .map(message::from_json)
        .collect::<Option<_>>()
        .ok_or_else(|| refuse(format!("{place} has an undecodable message")))?;
    Ok(Turn {
        conversation_id: conversation_id.to_string(),
        turn,
        messages,
        created_at,
        actor,
        redacted,
    })
}

/// One projection row: a turn, a span within it, one of the three kinds.
pub fn parse_projection(
    line: &str,
    number: usize,
    conversation_id: &str,
) -> Result<Projection, Refused> {
    let place = format!("projection line {number}");
    let data = parse_object(line, &place, conversation_id)?;
    let turn = data.get("turn").and_then(Value::as_u64);
    let span = data.get("span").and_then(Value::as_u64);
    let kind = data
        .get("kind")
        .and_then(Value::as_str)
        .filter(|kind| matches!(*kind, "log" | "digest" | "epoch"));
    let text = data.get("text").and_then(Value::as_str);
    match (turn, span, kind, text) {
        (Some(turn), Some(span), Some(_), Some(text)) if 1 <= span && span <= turn => {
            Ok(Projection {
                turn,
                text: text.to_string(),
                span,
            })
        }
        _ => Err(Refused::format_unsupported(
            conversation_id,
            &format!("{place} has invalid fields"),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ROW: &str = r#"{"neosian_format":1,"turn":2,"created_at":"2026-10-12T22:27:56.068674Z","messages":[{"role":"user","content":"hi","reasoning":null,"tool_calls":[],"tool_call_id":null}],"actor":"claude-code:s"}"#;

    fn reason(error: Refused) -> String {
        let prefix = "Unsupported turn format in conversation 'c': ";
        error
            .message
            .strip_prefix(prefix)
            .unwrap_or(&error.message)
            .to_string()
    }

    #[test]
    fn a_turn_reads_as_the_python_side_writes_it() {
        let turn = parse_turn(ROW, 2, "c").unwrap();
        assert_eq!(render_turn(&turn), format!("{ROW}\n"));
        let call = r#"{"neosian_format":1,"turn":1,"created_at":"2026-10-12T22:27:56Z","messages":[{"role":"user","content":"caf\u00e9","reasoning":null,"tool_calls":[],"tool_call_id":null},{"role":"assistant","content":null,"reasoning":null,"tool_calls":[{"id":"t1","name":"Read","arguments":{"b":1,"a":"x"}}],"tool_call_id":null},{"role":"tool","content":"ok","reasoning":null,"tool_calls":[],"tool_call_id":"t1"}],"actor":null}"#;
        assert_eq!(
            render_turn(&parse_turn(call, 1, "c").unwrap()),
            format!("{call}\n")
        );
        assert_eq!(
            (turn.turn, turn.actor.as_deref(), turn.redacted),
            (2, Some("claude-code:s"), false)
        );
        assert_eq!(turn.created_at.z(), "2026-10-12T22:27:56.068674Z");
        assert_eq!(turn.messages.len(), 1);
        let skeleton = r#"{"neosian_format":1,"turn":3,"created_at":"2026-10-12T22:27:56Z","messages":[],"actor":null,"redacted":true}"#;
        let turn = parse_turn(skeleton, 3, "c").unwrap();
        assert!(turn.redacted && turn.messages.is_empty() && turn.actor.is_none());
        let entry = parse_projection(
            r#"{"neosian_format":1,"turn":6,"span":4,"kind":"epoch","text":"folded"}"#,
            1,
            "c",
        )
        .unwrap();
        assert_eq!(
            (entry.turn, entry.span, entry.text.as_str()),
            (6, 4, "folded")
        );
    }

    #[test]
    fn a_malformed_row_is_refused_and_never_skipped() {
        let with = |field: &str, value: &str| {
            let mut data: Map<String, Value> = serde_json::from_str(ROW).unwrap();
            data.insert(field.to_string(), serde_json::from_str(value).unwrap());
            Value::Object(data).to_string()
        };
        for (line, why) in [
            ("{".to_string(), "malformed turn-log line 4"),
            ("7".to_string(), "turn-log line 4 is not an object"),
            (
                with("neosian_format", "1.0"),
                "turn-log line 4 missing format",
            ),
            (
                with("neosian_format", "2"),
                "turn-log line 4 format 2 is newer than supported",
            ),
            (with("turn", "0"), "turn-log line 4 has invalid fields"),
            (with("turn", "true"), "turn-log line 4 has invalid fields"),
            (with("messages", "[]"), "turn-log line 4 has invalid fields"),
            (
                with("redacted", "true"),
                "turn-log line 4 has invalid fields",
            ),
            (with("actor", "3"), "turn-log line 4 has invalid fields"),
            (
                with("created_at", "null"),
                "turn-log line 4 has invalid fields",
            ),
            (
                with("created_at", "\"soon\""),
                "turn-log line 4 has an unreadable timestamp",
            ),
            (
                with("created_at", "\"2026-10-12T22:27:56\""),
                "turn-log line 4 has a naive timestamp",
            ),
            (
                with("messages", r#"[{"role": "bot"}]"#),
                "turn-log line 4 has an undecodable message",
            ),
        ] {
            assert_eq!(
                parse_turn(&line, 4, "c").map_err(reason).unwrap_err(),
                why,
                "{line}"
            );
        }
        let entry = |line: &str| parse_projection(line, 1, "c").map_err(reason).unwrap_err();
        assert_eq!(
            entry(r#"{"neosian_format":1,"turn":2,"span":3,"kind":"log","text":""}"#),
            "projection line 1 has invalid fields"
        );
        assert_eq!(
            entry(r#"{"neosian_format":1,"turn":2,"span":1,"kind":"fold","text":""}"#),
            "projection line 1 has invalid fields"
        );
    }
}
