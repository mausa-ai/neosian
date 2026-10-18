//! Log-projection rendering (`conversation/projection.py`, DESIGN §9.6):
//! pure functions over turns and entries, every clip atom-safe (§23).
//! Role labels are full words; a redacted turn is the one word.

use std::collections::HashMap;

use serde_json::Value;

use super::links::{LinkRegistry, boundary};
use super::message::{Message, Role, text_of};
use super::{Projection, Turn};
use crate::pyfmt::{dumps, py_str};
use crate::text::{flatten, head, len, lstrip, rstrip, strip, tail};

// Tool segments of a log line: the args digest and the result head/tail
// are bounded independently of digest_chars — they identify the round,
// recall_turn carries it.
const ARGS_CHARS: usize = 80;
const RESULT_HEAD: usize = 100;
const RESULT_TAIL: usize = 40;
const SEPARATOR: &str = " | ";
/// What every renderer says for a turn the eraser blanked (§38).
pub const REDACTED: &str = "[redacted]";

/// Whitespace-flattened, contracted when `links` is given, head-clipped
/// at an atom boundary: a clip never splits a URL, path, id or handle; a
/// token that cannot fit is dropped whole behind the marker.
pub fn one_line(text: &str, limit: usize, links: Option<&LinkRegistry>, marker: &str) -> String {
    let mut flat = flatten(text);
    if let Some(links) = links {
        flat = links.contract(&flat);
    }
    if len(&flat) <= limit {
        return flat;
    }
    let cut = boundary(&flat, limit).0;
    lstrip(&format!("{}{marker}", rstrip(head(&flat, cut)))).to_string()
}

/// Winning entry index per covered turn: widest span, ties to later.
pub fn select(entries: &[Projection]) -> HashMap<u64, usize> {
    let mut best: HashMap<u64, (u64, usize)> = HashMap::new();
    for (index, entry) in entries.iter().enumerate() {
        for turn in (entry.turn - entry.span + 1)..=entry.turn {
            let key = (entry.span, index);
            if best.get(&turn).is_none_or(|held| key >= *held) {
                best.insert(turn, key);
            }
        }
    }
    best.into_iter()
        .map(|(turn, (_, index))| (turn, index))
        .collect()
}

/// The entry's log line with its turn-ref label: `[7]` / `[3-6]`; a
/// blanked entry is its label alone.
pub fn entry_line(entry: &Projection) -> String {
    let label = if entry.span == 1 {
        format!("[{}]", entry.turn)
    } else {
        format!("[{}-{}]", entry.turn - entry.span + 1, entry.turn)
    };
    rstrip(&format!("{label} {}", entry.text)).to_string()
}

/// The turn's tool results by call id, the last one wins.
pub fn results(turn: &Turn) -> HashMap<&str, &Message> {
    turn.messages
        .iter()
        .filter(|message| message.role == Role::Tool)
        .filter_map(|message| message.tool_call_id.as_deref().map(|id| (id, message)))
        .collect()
}

/// One deterministic log line for a turn, in provider order: USER text
/// verbatim up to `user_chars` then head-clipped with a recall pointer,
/// AGENT prose clipped at `digest_chars`, each tool round one segment.
pub fn log_line(
    turn: &Turn,
    digest_chars: usize,
    user_chars: usize,
    links: &LinkRegistry,
) -> String {
    if turn.redacted {
        return REDACTED.to_string();
    }
    let results = results(turn);
    let mut segments = Vec::new();
    for message in &turn.messages {
        match message.role {
            Role::User => {
                let marker = format!(" … [recall_turn({})]", turn.turn);
                let text = one_line(&text_of(message), user_chars, Some(links), &marker);
                segments.push(format!("USER: {text}"));
            }
            Role::Assistant => {
                let prose = text_of(message);
                let prose = strip(&prose);
                if !prose.is_empty() {
                    let text = one_line(prose, digest_chars, Some(links), " …");
                    segments.push(format!("AGENT: {text}"));
                }
                for call in &message.tool_calls {
                    let result = results.get(call.id.as_str()).copied();
                    segments.push(tool_segment(&call.name, &call.arguments, result, links));
                }
            }
            Role::System | Role::Tool => {}
        }
    }
    segments.join(SEPARATOR)
}

/// One tool round as a line: `TOOL name(args digest) → result head/tail`,
/// the round identified, never carried.
pub fn tool_segment(
    name: &str,
    arguments: &Value,
    result: Option<&Message>,
    links: &LinkRegistry,
) -> String {
    let args = one_line(&dumps(arguments, true), ARGS_CHARS, Some(links), " …");
    let outcome = match result {
        Some(result) => result_digest(&text_of(result), links),
        None => "?".to_string(),
    };
    format!("TOOL {name}({args}) → {outcome}")
}

/// An envelope's `data` or `error`, else the raw text, head and tail.
fn result_digest(raw: &str, links: &LinkRegistry) -> String {
    if let Ok(Value::Object(payload)) = serde_json::from_str::<Value>(raw) {
        if payload.get("success") == Some(&Value::Bool(false)) {
            let error = py_str(payload.get("error").unwrap_or(&Value::Null));
            return head_tail(&format!("error: {error}"), links);
        }
        if let Some(data) = payload.get("data") {
            return head_tail(&py_str(data), links);
        }
    }
    head_tail(raw, links)
}

/// Head and tail, each cut moved off any atom it lands in: the head ends
/// before it, the tail starts after it, dropped whole, never split.
fn head_tail(text: &str, links: &LinkRegistry) -> String {
    let flat = links.contract(&flatten(text));
    let total = len(&flat);
    if total <= RESULT_HEAD + RESULT_TAIL {
        return flat;
    }
    let head_at = boundary(&flat, RESULT_HEAD).0;
    let tail_at = boundary(&flat, total - RESULT_TAIL).1;
    format!(
        "{} … {}",
        rstrip(head(&flat, head_at)),
        lstrip(tail(&flat, tail_at))
    )
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::super::message::from_json;
    use super::*;
    use crate::store::time::Stamp;

    fn make(messages: &[Value]) -> Turn {
        Turn {
            conversation_id: "c".to_string(),
            turn: 4,
            messages: messages.iter().map(|m| from_json(m).unwrap()).collect(),
            created_at: Stamp::now(),
            actor: None,
            redacted: false,
        }
    }

    #[test]
    fn a_line_clips_at_an_atom_and_carries_the_marker() {
        let none = LinkRegistry::of(&[], None);
        assert_eq!(one_line("  a \n b  ", 10, Some(&none), " …"), "a b");
        assert_eq!(
            one_line("word https://example.com/path tail", 12, Some(&none), " …"),
            "word …"
        );
        assert_eq!(
            one_line("https://example.com/path tail", 5, None, " …"),
            "…"
        );
        assert_eq!(
            one_line("abcdefghij", 5, None, " … [recall_turn(2)]"),
            "abcde … [recall_turn(2)]"
        );
    }

    #[test]
    fn the_winning_entry_is_the_widest_then_the_later() {
        let entry = |turn, text: &str, span| Projection {
            turn,
            kind: "log".into(),
            text: text.into(),
            span,
        };
        let entries = vec![
            entry(2, "a", 1),
            entry(3, "b", 3),
            entry(3, "c", 1),
            entry(5, "d", 2),
            entry(5, "e", 2),
        ];
        let winner = select(&entries);
        assert_eq!(
            (winner[&1], winner[&2], winner[&3], winner[&4], winner[&5]),
            (1, 1, 1, 4, 4)
        );
        assert_eq!(entry_line(&entries[1]), "[1-3] b");
        assert_eq!(entry_line(&entry(7, "  ", 1)), "[7]");
    }

    #[test]
    fn a_log_line_is_the_turn_in_provider_order() {
        let long = "x".repeat(300);
        let turn = make(&[
            json!({"role": "user", "content": "do the thing"}),
            json!({"role": "assistant", "content": "  thinking  ", "tool_calls": [{"id": "t1", "name": "run", "arguments": {"cmd": "ls", "n": 1}}, {"id": "t2", "name": "lost"}]}),
            json!({"role": "tool", "content": format!("{{\"success\": true, \"data\": \"{long}\"}}"), "tool_call_id": "t1"}),
            json!({"role": "assistant", "content": "done"}),
        ]);
        let links = LinkRegistry::of(std::slice::from_ref(&turn), None);
        let line = log_line(&turn, 200, 800, &links);
        let head = "USER: do the thing | AGENT: thinking | TOOL run({\"cmd\":\"ls\",\"n\":1}) → ";
        assert!(line.starts_with(head), "{line}");
        assert!(line.contains(&format!("{} … {}", "x".repeat(100), "x".repeat(40))));
        assert!(
            line.ends_with(" | TOOL lost({}) → ? | AGENT: done"),
            "{line}"
        );
        let failed = make(&[
            json!({"role": "assistant", "tool_calls": [{"id": "t", "name": "f", "arguments": {}}]}),
            json!({"role": "tool", "content": "{\"success\": false, \"error\": null}", "tool_call_id": "t"}),
        ]);
        assert_eq!(
            log_line(&failed, 200, 800, &links),
            "TOOL f({}) → error: None"
        );
        let redacted = Turn {
            redacted: true,
            ..turn
        };
        assert_eq!(log_line(&redacted, 200, 800, &links), REDACTED);
    }
}
