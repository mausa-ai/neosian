//! Link handles (`conversation/links.py`, DESIGN §23): the atom grammar,
//! one regex carried verbatim from the Python side onto a backtracking
//! engine with the same semantics (#359), the clip boundary, and the
//! registry that numbers a history's long tokens by first appearance.

use std::collections::HashMap;
use std::sync::OnceLock;

use fancy_regex::{Captures, Regex, RegexBuilder};
use serde_json::Value;

use super::Turn;
use super::message::text_of;

/// A token registers as a link from this length.
pub const LINK_CHARS: usize = 48;
/// Python's `\s` inside a class: White_Space and the four separators
/// U+001C to U+001F that `str.isspace` counts.
const SPACE: &str = r"\s\x1c-\x1f";

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
/// a clip cuts at the start or the end of that span, never within.
pub fn boundary(text: &str, at: usize) -> (usize, usize) {
    let mut cursor = Cursor {
        text,
        byte: 0,
        chars: 0,
    };
    for found in atom().find_iter(text).flatten() {
        let start = cursor.at(found.start());
        let end = cursor.at(found.end());
        if start < at && at < end {
            return (start, end);
        }
        if start >= at {
            break;
        }
    }
    (at, at)
}

/// The registrable atoms of `text`, in order: long, and not a handle.
fn links(text: &str) -> impl Iterator<Item = &str> {
    atom()
        .find_iter(text)
        .flatten()
        .map(|found| found.as_str())
        .filter(|token| token.chars().count() >= LINK_CHARS && !token.starts_with("[link "))
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
    /// skipped.
    pub fn contract(&self, text: &str) -> String {
        if self.numbers.is_empty() {
            return text.to_string();
        }
        let swap = |caps: &Captures<str>| {
            let token = caps.get(0).map_or("", |found| found.as_str());
            self.numbers
                .get(token)
                .map_or_else(|| token.to_string(), |number| self.handle(*number))
        };
        atom().replace_all(text, swap).into_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn atoms(text: &str) -> Vec<&str> {
        atom()
            .find_iter(text)
            .flatten()
            .map(|found| found.as_str())
            .collect()
    }

    #[test]
    fn a_token_is_one_atom_and_punctuation_stays_outside() {
        for token in [
            "https://example.com/a/b?q=1#x",
            "/var/log/neosian/store.lock",
            "01HZX4YQ6B3K2M8R9T0V1W2X3Y",
            "https://en.wikipedia.org/wiki/Foo_(bar)",
            "[link 3]",
        ] {
            assert_eq!(atoms(token), [token], "{token}");
            assert_eq!(atoms(&format!("see {token}.")), [token], "{token}");
            assert_eq!(atoms(&format!("({token}),")), [token], "{token}");
        }
        assert!(atoms("plain words only here").is_empty());
        assert_eq!(
            atoms("\"https://x.y/z\" and `a/b`"),
            ["https://x.y/z", "a/b"]
        );
        assert_eq!(atoms("v1.2.3. next"), ["v1.2.3"]);
        assert_eq!(atoms("ab/cd"), ["ab/cd"]);
        assert!(atoms("xhttps://a.b/c").len() == 1);
    }

    #[test]
    fn a_boundary_is_the_atom_the_cut_falls_in() {
        let text = "see https://example.com/a/b now";
        assert_eq!(boundary(text, 10), (4, 27));
        assert_eq!(boundary(text, 4), (4, 4));
        assert_eq!(boundary(text, 27), (27, 27));
        assert_eq!(boundary("café /x/y end", 7), (5, 9));
        assert_eq!(boundary("", 0), (0, 0));
    }

    #[test]
    fn a_registry_numbers_long_tokens_by_first_appearance() {
        let long = format!("https://example.com/{}", "a".repeat(40));
        let turns = vec![Turn {
            conversation_id: "c".to_string(),
            turn: 1,
            messages: vec![
                super::super::message::from_json(&serde_json::json!({
                    "role": "user", "content": format!("read {long} and {long} and /short")
                }))
                .unwrap(),
            ],
            created_at: crate::store::time::Stamp::now(),
            actor: None,
            redacted: false,
        }];
        let links = LinkRegistry::of(&turns, Some("c"));
        assert_eq!(links.handle(1), "[link c:1]");
        assert_eq!(
            links.contract(&format!("at {long}. [link 9] /short")),
            "at [link c:1]. [link 9] /short"
        );
        let none = LinkRegistry::of(&[], None);
        assert_eq!(none.contract(&long), long);
    }
}
