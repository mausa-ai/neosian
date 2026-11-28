//! What a harness's headless output says, harness by harness reduced to
//! one vocabulary, and what a turn keeps of it (§40.50 items 8, 11): the
//! child's own words in one bounded buffer, what neosian observed (a tool
//! call, a command, a denial) as structured entries in another. A label is
//! never inserted into the child's text.

use std::collections::VecDeque;

use serde_json::{Value, json};

/// One thing a line of a harness's output said.
#[derive(Clone, Debug, PartialEq)]
pub enum Event {
    /// The harness's own session ID.
    Session(String),
    /// Text the child agent wrote; the last one is the answer unless an
    /// `Answer` follows.
    Text(String),
    /// The harness's own final answer for the turn.
    Answer(String),
    /// What neosian observed, as one structured entry (`kind` and fields).
    Observed(Value),
    /// The harness reported that the turn failed.
    Failure(String),
    /// The harness reported, in its own structured error, that its sign-in
    /// failed: the turn failed, and the sentence says how to sign in (X4c).
    SignIn(String),
}

/// The text's head, kept whole.
pub const HEAD_BYTES: usize = 64 * 1024;
/// The text's tail, the newest bytes.
pub const TAIL_BYTES: usize = 192 * 1024;
/// The most one answer returns of new text and of the final answer.
pub const OUTPUT_BYTES: usize = 16 * 1024;
/// The longest string one observed entry carries.
pub const FIELD_BYTES: usize = 400;
/// The longest failure or error text kept.
pub const ERROR_BYTES: usize = 4 * 1024;
/// How many observed entries a turn keeps, and returns in one answer.
pub const EVENTS_KEPT: usize = 256;
pub const EVENTS_PER_ANSWER: usize = 32;

/// A prefix of `text` of at most `limit` bytes, on a character boundary.
pub fn head(text: &str, limit: usize) -> &str {
    if text.len() <= limit {
        return text;
    }
    let mut end = limit;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

/// A suffix of `text` of at most `limit` bytes, on a character boundary.
pub fn tail(text: &str, limit: usize) -> &str {
    if text.len() <= limit {
        return text;
    }
    let mut start = text.len() - limit;
    while !text.is_char_boundary(start) {
        start += 1;
    }
    &text[start..]
}

/// One observed entry whose strings are bounded.
pub fn observed(kind: &str, fields: &[(&str, Value)]) -> Event {
    let mut entry = serde_json::Map::new();
    entry.insert("kind".into(), json!(kind));
    for (name, value) in fields {
        let value = match value {
            Value::String(text) => json!(head(text, FIELD_BYTES)),
            other => other.clone(),
        };
        entry.insert((*name).to_string(), value);
    }
    Event::Observed(Value::Object(entry))
}

/// The child's words, bounded: offsets are byte positions in the whole
/// stream, so a reader's position stays valid as the middle drops. The
/// tail is a queue of pieces, so dropping its front moves nothing else.
#[derive(Default)]
pub struct Text {
    head: String,
    tail: VecDeque<String>,
    tail_bytes: usize,
    total: usize,
}

impl Text {
    pub fn push(&mut self, text: &str) {
        self.total += text.len();
        if self.tail.is_empty() && self.head.len() + text.len() <= HEAD_BYTES {
            self.head.push_str(text);
            return;
        }
        let rest = if self.tail.is_empty() {
            let room = head(text, HEAD_BYTES - self.head.len());
            self.head.push_str(room);
            &text[room.len()..]
        } else {
            text
        };
        self.tail.push_back(rest.to_string());
        self.tail_bytes += rest.len();
        while self.tail_bytes > TAIL_BYTES {
            let front = self.tail.front_mut().expect("bytes over the bound");
            let excess = self.tail_bytes - TAIL_BYTES;
            if front.len() <= excess {
                self.tail_bytes -= front.len();
                self.tail.pop_front();
            } else {
                let kept = tail(front, front.len() - excess).to_string();
                self.tail_bytes -= front.len() - kept.len();
                *front = kept;
            }
        }
    }

    pub fn total(&self) -> usize {
        self.total
    }

    /// What arrived after position `from`: the newest `limit` bytes of what
    /// is still held, and how many bytes after `from` it leaves out.
    pub fn since(&self, from: usize, limit: usize) -> (String, usize) {
        let from = from.min(self.total);
        let tail_start = self.total - self.tail_bytes;
        let mut held = String::new();
        if from < self.head.len() {
            held.push_str(&self.head[from..]);
        }
        let mut at = tail_start;
        for piece in &self.tail {
            let end = at + piece.len();
            if end > from {
                held.push_str(&piece[from.saturating_sub(at)..]);
            }
            at = end;
        }
        let shown = tail(&held, limit).to_string();
        let skipped = self.total - from - shown.len();
        (shown, skipped)
    }
}

/// Observed entries, the newest kept, numbered from the first.
#[derive(Default)]
pub struct Log {
    entries: VecDeque<Value>,
    total: usize,
}

impl Log {
    pub fn push(&mut self, entry: Value) {
        self.entries.push_back(entry);
        self.total += 1;
        if self.entries.len() > EVENTS_KEPT {
            self.entries.pop_front();
        }
    }

    pub fn total(&self) -> usize {
        self.total
    }

    /// The entries after number `from`, at most `limit` of the newest, and
    /// how many after `from` are left out.
    pub fn since(&self, from: usize, limit: usize) -> (Vec<Value>, usize) {
        let first = self.total - self.entries.len();
        let held: Vec<&Value> = self
            .entries
            .iter()
            .enumerate()
            .filter(|(index, _)| first + index >= from)
            .map(|(_, entry)| entry)
            .collect();
        let shown: Vec<Value> = held[held.len().saturating_sub(limit)..]
            .iter()
            .map(|entry| (*entry).clone())
            .collect();
        let skipped = self.total - from.min(self.total) - shown.len();
        (shown, skipped)
    }
}

/// The last bytes of a stream, for a harness's stderr.
#[derive(Default)]
pub struct Tail(String);

impl Tail {
    pub fn push(&mut self, text: &str) {
        self.0.push_str(text);
        if self.0.len() > 2 * ERROR_BYTES {
            let keep = tail(&self.0, ERROR_BYTES).len();
            self.0.drain(..self.0.len() - keep);
        }
    }

    pub fn text(&self) -> &str {
        tail(&self.0, ERROR_BYTES)
    }

    /// Keep only `text`, as when the watcher's own line is taken off.
    pub fn replace(&mut self, text: &str) {
        self.0 = text.to_string();
    }
}

#[cfg(test)]
mod tests;
