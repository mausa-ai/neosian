//! Link handles (`conversation/links.py`, DESIGN §23): the atom grammar,
//! one regex carried verbatim from the Python side onto a backtracking
//! engine with the same semantics (#359), the clip boundary, and the
//! registry that numbers a history's long tokens by first appearance.
//! The scans are bounded to where an atom can be (§39.10): no atom but
//! a handle holds a delimiter, so a registrable token lives inside a run
//! of non-delimiter characters at least `LINK_CHARS` long, and a clip's
//! atom inside the run around the clip; the engine never reads a long
//! result whole, and the answer is the full scan's, pinned against it.

use std::collections::HashMap;
use std::sync::OnceLock;

use fancy_regex::{Regex, RegexBuilder};
use serde_json::Value;

use super::Turn;
use super::message::text_of;
use crate::text::is_space;

/// A token registers as a link from this length.
pub const LINK_CHARS: usize = 48;
/// Python's `\s` inside a class: White_Space and the four separators
/// U+001C to U+001F that `str.isspace` counts.
const SPACE: &str = r"\s\x1c-\x1f";
/// A handle's opening: the one atom that may span a delimiter.
const HANDLE_OPEN: &str = "[link ";

/// `_ATOM`: a handle, or a URL, path or id not preceded by a token
/// character; a token ends where trailing punctuation meets a delimiter.
fn atom() -> &'static Regex {
    static ATOM: OnceLock<Regex> = OnceLock::new();
    ATOM.get_or_init(|| {
        let handle = r"\[link [^\]]*\]";
        let character = format!(r#"[^{SPACE}"'<>|\[\]`\\()]"#);
        let unit = format!(r"(?:{character}|\({character}*\))");
        let end = format!(r#"(?=[.,;:!?)]*(?:[{SPACE}"'<>|\[\]`\\)]|$))"#);
        let url = format!("[A-Za-z][A-Za-z0-9+.-]*://{unit}+?{end}");
        let path = format!("{unit}*?/{unit}*?{end}");
        let id =
            format!(r"(?=[A-Za-z0-9_.-]*\d)[A-Za-z0-9_-](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?{end}");
        RegexBuilder::new(&format!("{handle}|(?<!{character})(?:{url}|{path}|{id})"))
            .backtrack_limit(usize::MAX)
            .build()
            .expect("the atom grammar compiles")
    })
}

/// What no atom but a handle holds: the characters `character` excludes,
/// the parentheses apart (they sit inside a token as a balanced group).
fn delimiter(ch: char) -> bool {
    is_space(ch) || "\"'<>|[]`\\".contains(ch)
}

/// The maximal runs of non-delimiter characters, as byte ranges.
fn runs(text: &str) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut start = None;
    for (at, ch) in text.char_indices() {
        match (delimiter(ch), start) {
            (true, Some(from)) => {
                runs.push((from, at));
                start = None;
            }
            (false, None) => start = Some(at),
            _ => {}
        }
    }
    if let Some(from) = start {
        runs.push((from, text.len()));
    }
    runs
}

/// Whether a handle opened before `at` is still open there: the one
/// atom that may span a delimiter, so no scan restarts past it.
fn handle_open_at(text: &str, at: usize) -> bool {
    text.match_indices(HANDLE_OPEN)
        .take_while(|(open, _)| *open < at)
        .last()
        .is_some_and(|(open, _)| !text[(open + HANDLE_OPEN.len()).min(at)..at].contains(']'))
}

/// The run `at` falls in: from after the delimiter before it to the
/// delimiter at or after it.
fn run_around(text: &str, at: usize) -> (usize, usize) {
    let from = text[..at]
        .char_indices()
        .rev()
        .find(|(_, ch)| delimiter(*ch))
        .map_or(0, |(index, ch)| index + ch.len_utf8());
    let until = text[at..]
        .char_indices()
        .find(|(_, ch)| delimiter(*ch))
        .map_or(text.len(), |(index, _)| at + index);
    (from, until)
}

/// The atoms of `text[..until]` from `from` on, in scan order: the full
/// scan's matches there, since no atom crosses the delimiter before
/// `from` or at `until`.
fn scan(text: &str, from: usize, until: usize) -> Vec<(usize, usize)> {
    let window = &text[..until];
    let mut found = Vec::new();
    let mut pos = from;
    while let Ok(Some(matched)) = atom().find_from_pos(window, pos) {
        found.push((matched.start(), matched.end()));
        pos = matched.end();
    }
    found
}

/// Byte offsets of `text` to character indices, counted from a cursor.
struct Cursor<'a> {
    text: &'a str,
    byte: usize,
    chars: usize,
}

impl Cursor<'_> {
    fn at(&mut self, byte: usize) -> usize {
        self.chars += self.text[self.byte..byte].chars().count();
        self.byte = byte;
        self.chars
    }
}

/// The span (in characters) of the atom `at` falls inside, or `(at, at)`:
/// a clip cuts at the start or the end of that span, never within. Only
/// the run around `at` is scanned, the whole text when a handle is open
/// there.
pub fn boundary(text: &str, at: usize) -> (usize, usize) {
    let at_byte = text
        .char_indices()
        .nth(at)
        .map_or(text.len(), |(byte, _)| byte);
    let (from, until) = if handle_open_at(text, at_byte) {
        (0, text.len())
    } else {
        run_around(text, at_byte)
    };
    let mut cursor = Cursor {
        text,
        byte: 0,
        chars: 0,
    };
    for (first, last) in scan(text, from, until) {
        let start = cursor.at(first);
        let end = cursor.at(last);
        if start < at && at < end {
            return (start, end);
        }
        if start >= at {
            break;
        }
    }
    (at, at)
}

/// Is a token a link: long, and not a handle.
fn registrable(token: &str) -> bool {
    token.chars().count() >= LINK_CHARS && !token.starts_with(HANDLE_OPEN)
}

/// The runs long enough to hold a link; None when a handle, which may
/// span a run, is in the text, and the whole of it is the one run.
fn long_runs(text: &str) -> Vec<(usize, usize)> {
    if text.contains(HANDLE_OPEN) {
        return vec![(0, text.len())];
    }
    let long = |(from, until): &(usize, usize)| text[*from..*until].chars().count() >= LINK_CHARS;
    runs(text).into_iter().filter(long).collect()
}

/// The registrable atoms of `text`, in order.
fn links(text: &str) -> Vec<&str> {
    long_runs(text)
        .into_iter()
        .flat_map(|(from, until)| scan(text, from, until))
        .map(|(first, last)| &text[first..last])
        .filter(|token| registrable(token))
        .collect()
}

/// Every string inside a JSON value, in order.
fn strings<'a>(value: &'a Value, found: &mut Vec<&'a str>) {
    match value {
        Value::String(text) => found.push(text),
        Value::Object(map) => map.values().for_each(|item| strings(item, found)),
        Value::Array(items) => items.iter().for_each(|item| strings(item, found)),
        _ => {}
    }
}

/// Tokens numbered by first appearance; `source` names another
/// conversation and qualifies its handles.
pub struct LinkRegistry {
    numbers: HashMap<String, usize>,
    label: String,
}

impl LinkRegistry {
    /// Turns in order, messages in provider order, a message's text then
    /// its tool-call argument strings; reasoning is model-internal and
    /// skipped.
    pub fn of(turns: &[Turn], source: Option<&str>) -> Self {
        let mut numbers: HashMap<String, usize> = HashMap::new();
        let mut register = |text: &str| {
            for token in links(text) {
                let next = numbers.len() + 1;
                numbers.entry(token.to_string()).or_insert(next);
            }
        };
        for turn in turns {
            for message in &turn.messages {
                register(&text_of(message));
                for call in &message.tool_calls {
                    let mut found = Vec::new();
                    strings(&call.arguments, &mut found);
                    found.into_iter().for_each(&mut register);
                }
            }
        }
        Self {
            numbers,
            label: source.map_or_else(String::new, |source| format!("{source}:")),
        }
    }

    pub fn handle(&self, number: usize) -> String {
        format!("[link {}{number}]", self.label)
    }

    /// Every registered token → its handle, by whole-atom match; an
    /// unregistered token or an existing handle passes through. With
    /// nothing registered the text is its own answer, so the scan is
    /// skipped; otherwise only the runs long enough to hold a link are.
    pub fn contract(&self, text: &str) -> String {
        if self.numbers.is_empty() {
            return text.to_string();
        }
        let mut out = String::with_capacity(text.len());
        let mut done = 0;
        for (from, until) in long_runs(text) {
            out.push_str(&text[done..from]);
            let mut pos = from;
            for (first, last) in scan(text, from, until) {
                out.push_str(&text[pos..first]);
                let token = &text[first..last];
                match self.numbers.get(token) {
                    Some(number) => out.push_str(&self.handle(*number)),
                    None => out.push_str(token),
                }
                pos = last;
            }
            out.push_str(&text[pos..until]);
            done = until;
        }
        out.push_str(&text[done..]);
        out
    }
}

#[cfg(test)]
mod tests;
