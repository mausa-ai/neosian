//! A queued turn's draft id (DESIGN §40.54 item 17.3, ruling D1 of the
//! T1e1 plan). A closed span's first line is its opening line,
//! `{"kind": "span", "opened_at": "<UTC>", "nonce": "<uuid v4>"}`, so two
//! turns of one conversation opened by the same prompt still differ. The
//! draft id is `p` and the first 32 hex digits of the SHA-256 of the
//! conversation id, a line feed and that first line; the fresh id of the
//! one retry 17.4 allows is the same digest over the line followed by
//! `\n1`. The file never changes once closed, so a lost state derives the
//! same id again.

use serde_json::{Map, Value, json};

use super::super::fingerprint::digest;
use crate::pyfmt::dumps_spaced;
use crate::store::time::Stamp;

/// The `kind` of the opening line; `messages_of` and `last_prompt` skip it.
pub const OPENING: &str = "span";

/// A stamp in UTC with six fraction digits and `Z`.
pub fn micros_z(stamp: Stamp) -> String {
    let text = stamp.z();
    match text.find('.') {
        Some(_) => text,
        None => text.replacen('Z', ".000000Z", 1),
    }
}

/// The opening line for a span opened at `opened`, without its line feed.
pub fn opening_line(opened: Stamp, nonce: &str) -> String {
    let mut line = Map::new();
    line.insert("kind".into(), json!(OPENING));
    line.insert("opened_at".into(), json!(micros_z(opened)));
    line.insert("nonce".into(), json!(nonce));
    dumps_spaced(&Value::Object(line), false, false)
}

/// The first line of a span's text, without its line feed.
#[cfg(test)]
pub fn first_line(text: &str) -> &str {
    text.split('\n').next().unwrap_or("")
}

/// Whether `line` is an opening line.
pub fn is_opening(line: &str) -> bool {
    serde_json::from_str::<Value>(line)
        .ok()
        .is_some_and(|value| value.get("kind").and_then(Value::as_str) == Some(OPENING))
}

fn id_of(material: &str) -> String {
    format!("p{}", &digest(material.as_bytes())[..32])
}

/// The draft id of a queued turn of `conversation` whose first line is `line`.
pub fn draft_id(conversation: &str, line: &str) -> String {
    id_of(&format!("{conversation}\n{line}"))
}

/// The id a turn is written under again after `draft_mismatch` or
/// `draft_size` (17.4), once. Its first caller is the records landing of
/// T1e1c; until then only the tests use it.
#[allow(dead_code)]
pub fn fresh_id(conversation: &str, line: &str) -> String {
    id_of(&format!("{conversation}\n{line}\n1"))
}
