//! How a search answers (`conversation/hits.py`, DESIGN §32): a header
//! naming the turn, and a snippet, the line holding the most terms
//! windowed around its earliest match with the edges moved off any atom
//! they land in (§23). Pure functions, no I/O.

use fancy_regex::Regex;
use serde_json::{Value, json};

use super::Turn;
use super::links::boundary;
use super::search::turn_text;
use crate::text::{flatten, head, len, splitlines, strip, tail};

pub const SNIPPET_CHARS: usize = 160;
// The match sits a quarter in; an atom longer than half the window is
// dropped whole rather than stretched into it.
const LEAD: usize = SNIPPET_CHARS / 4;
const ATOM_MAX: usize = SNIPPET_CHARS / 2;
const CUT: &str = "…";

pub fn hit_header(turn: &Turn) -> String {
    format!(
        "[{} #{}] {} {}",
        turn.conversation_id,
        turn.turn,
        turn.created_at.z(),
        turn.actor.as_deref().unwrap_or("-")
    )
}

/// `re.search(re.escape(term), flat, re.IGNORECASE)`: where the term
/// first matches, in characters.
fn find_ci(flat: &str, term: &str) -> Option<usize> {
    let pattern = Regex::new(&format!("(?i){}", fancy_regex::escape(term))).ok()?;
    let found = pattern.find(flat).ok().flatten()?;
    Some(len(&flat[..found.start()]))
}

/// The flattened line holding the most terms (the earliest on a tie) and
/// its earliest match's offset; the first line at 0 when none matches.
fn first_match(text: &str, terms: &[String]) -> (String, usize) {
    let mut lines: Vec<String> = splitlines(text).into_iter().map(flatten).collect();
    if lines.is_empty() {
        lines.push(String::new());
    }
    let mut best = (0, 0, 0);
    for (index, flat) in lines.iter().enumerate() {
        let starts: Vec<usize> = terms
            .iter()
            .filter_map(|term| find_ci(flat, term))
            .collect();
        if starts.len() > best.0 {
            best = (
                starts.len(),
                starts.iter().copied().min().unwrap_or(0),
                index,
            );
        }
    }
    (lines.swap_remove(best.2), best.1)
}

/// The matching line, whole when it fits, else `SNIPPET_CHARS` around the
/// first match with `…` marking each cut edge.
pub fn snippet(text: &str, terms: &[String]) -> String {
    let (flat, at) = first_match(text, terms);
    let total = len(&flat);
    if total <= SNIPPET_CHARS {
        return flat;
    }
    let start = at.saturating_sub(LEAD).min(total - SNIPPET_CHARS);
    let end = start + SNIPPET_CHARS;
    let (first, last) = (boundary(&flat, start), boundary(&flat, end));
    let lo = if first.1 - first.0 <= ATOM_MAX {
        first.0
    } else {
        first.1
    };
    let hi = if last.1 - last.0 <= ATOM_MAX {
        last.1
    } else {
        last.0
    };
    let piece = if lo < hi {
        strip(head(tail(&flat, lo), hi - lo))
    } else {
        ""
    };
    let before = if lo > 0 {
        format!("{CUT} ")
    } else {
        String::new()
    };
    let after = if hi < total {
        format!(" {CUT}")
    } else {
        String::new()
    };
    format!("{before}{piece}{after}")
}

pub fn hit_json(turn: &Turn, terms: &[String]) -> Value {
    json!({
        "conversation_id": turn.conversation_id,
        "turn": turn.turn,
        "created_at": turn.created_at.z(),
        "actor": turn.actor,
        "snippet": snippet(&turn_text(turn), terms),
    })
}

/// The shell's rendering: one line, the audit verb's two-space columns.
pub fn hit_line(turn: &Turn, terms: &[String]) -> String {
    format!("{}  {}", hit_header(turn), snippet(&turn_text(turn), terms))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn terms(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| word.to_string()).collect()
    }

    #[test]
    fn a_short_line_is_whole_and_the_fullest_line_wins() {
        assert_eq!(
            snippet("hello wide world", &terms(&["world"])),
            "hello wide world"
        );
        assert_eq!(
            snippet("alpha\nbeta  gamma\ngamma delta", &terms(&["gamma"])),
            "beta gamma"
        );
        let text = "the cache is tuned\nthe cache TTL is 86400\nttl noted";
        assert_eq!(
            snippet(text, &terms(&["cache", "ttl"])),
            "the cache TTL is 86400"
        );
        assert_eq!(
            snippet("first line\nsecond", &terms(&["zzz"])),
            "first line"
        );
        assert_eq!(snippet("", &terms(&["x"])), "");
    }

    #[test]
    fn a_long_line_is_windowed_a_quarter_in() {
        let words: Vec<String> = (0..80).map(|i| format!("w{i:03}")).collect();
        let text = words.join(" ").replace("w040", "NEEDLE");
        let clip = snippet(&text, &terms(&["needle"]));
        assert!(clip.starts_with("… ") && clip.ends_with(" …"), "{clip}");
        assert!(clip.contains("NEEDLE") && len(&clip) <= SNIPPET_CHARS + 2 * ATOM_MAX);
        assert!(clip.find("NEEDLE").unwrap() < clip.len() / 2);
        let head = snippet(&format!("needle {}", words.join(" ")), &terms(&["needle"]));
        assert!(head.starts_with("needle") && head.ends_with(" …"));
        let tail = snippet(&format!("{} needle", words.join(" ")), &terms(&["needle"]));
        assert!(tail.starts_with("… ") && tail.ends_with("needle"));
    }

    #[test]
    fn an_atom_at_the_edge_is_kept_or_dropped_whole() {
        let url = "https://example.com/a/b/c/d/e/f/g/h";
        let text = format!("needle {}{url} {}", "x ".repeat(70), "y ".repeat(60));
        assert!(snippet(&text, &terms(&["needle"])).contains(url));
        let blob = format!(
            "/{}",
            (0..40)
                .map(|i| format!("seg{i}"))
                .collect::<Vec<_>>()
                .join("/")
        );
        let text = format!("needle {}{blob} {}", "x ".repeat(70), "y ".repeat(60));
        let clip = snippet(&text, &terms(&["needle"]));
        assert!(!clip.contains("seg0") && clip.ends_with(" …"), "{clip}");
    }
}
