//! History search (`conversation/search.py`, `file_search.py`, DESIGN
//! §32): the one rule every substrate applies, and the file scan that
//! applies it here. A query is whitespace-split into lowercased terms; a
//! turn matches when every term is a substring of its folded searchable
//! text; hits come newest first under one total order. No ranking, no
//! wildcards, no stemming.

use std::fs;
use std::path::Path;

use super::ids::parse_conversation_id;
use super::message::{Message, text_of};
use super::rows::{TURNS, parse_turn};
use super::{Error, Turn};
use crate::pyfmt::dumps;
use crate::text::is_space;

/// The lowercased, whitespace-split terms; a blank query is refused.
pub fn parse_query(query: &str) -> Result<Vec<String>, String> {
    let terms: Vec<String> = query
        .to_lowercase()
        .split(is_space)
        .filter(|term| !term.is_empty())
        .map(str::to_string)
        .collect();
    if terms.is_empty() {
        return Err("query must contain at least one term".to_string());
    }
    Ok(terms)
}

/// The searchable text of a turn's messages: each message's text in
/// order, each tool call as its name and compact arguments, no role
/// labels (a query "user" must never match every turn).
pub fn messages_text(messages: &[Message]) -> String {
    let mut pieces: Vec<String> = Vec::new();
    for message in messages {
        let text = text_of(message);
        if !text.is_empty() {
            pieces.push(text.into_owned());
        }
        for call in &message.tool_calls {
            pieces.push(format!("{} {}", call.name, dumps(&call.arguments, false)));
        }
    }
    pieces.join("\n")
}

pub fn turn_text(turn: &Turn) -> String {
    messages_text(&turn.messages)
}

/// Every term a substring of the folded text.
pub fn match_terms(text: &str, terms: &[String]) -> bool {
    let folded = text.to_lowercase();
    terms.iter().all(|term| folded.contains(term.as_str()))
}

/// The total order: `created_at`, then the id in codepoint order, then
/// the number, each descending.
pub fn newest_first(turns: &mut [Turn]) {
    turns.sort_by(|a, b| {
        (b.created_at.instant(), &b.conversation_id, b.turn).cmp(&(
            a.created_at.instant(),
            &a.conversation_id,
            a.turn,
        ))
    });
}

/// Whether the file holds the term verbatim when a turn holds it:
/// printable ASCII free of `"` and `\`, everything else being escaped
/// by `json.dumps(ensure_ascii=True)`.
pub fn prefilterable(term: &str) -> bool {
    term.bytes()
        .all(|byte| (b' '..=b'~').contains(&byte) && byte != b'"' && byte != b'\\')
}

/// The matching turns of one log. A line is decoded only when it can
/// match: every prefilterable term must appear in the raw line first, so
/// a malformed line lacking one is invisible here while a candidate
/// malformed line is refused exactly as a read refuses it.
pub fn scan_turns(
    file: &Path,
    conversation_id: &str,
    terms: &[String],
) -> Result<Vec<Turn>, Error> {
    let raw: Vec<&str> = terms
        .iter()
        .map(String::as_str)
        .filter(|term| prefilterable(term))
        .collect();
    let text = fs::read_to_string(file)?;
    let mut found = Vec::new();
    for (index, line) in text.lines().enumerate() {
        let lowered = line.to_lowercase();
        if !raw.iter().all(|term| lowered.contains(term)) {
            continue;
        }
        let turn = parse_turn(line, index + 1, conversation_id)?;
        if match_terms(&turn_text(&turn), terms) {
            found.push(turn);
        }
    }
    Ok(found)
}

/// Every matching turn of the named conversations, or of every one under
/// `parent`, newest first, cut to `limit`.
pub fn search_files(
    parent: &Path,
    ids: Option<&[String]>,
    terms: &[String],
    limit: usize,
) -> Result<Vec<Turn>, Error> {
    let held;
    let ids = match ids {
        Some(ids) => ids,
        None => {
            held = held_ids(parent)?;
            &held
        }
    };
    let mut found = Vec::new();
    for conversation_id in ids {
        let file = parent.join(conversation_id).join(TURNS);
        if file.is_file() {
            found.extend(scan_turns(&file, conversation_id, terms)?);
        }
    }
    newest_first(&mut found);
    found.truncate(limit);
    Ok(found)
}

/// Every entry under `parent` with a grammatical name; a foreign name is
/// skipped, as listings skip foreign files.
pub fn held_ids(parent: &Path) -> Result<Vec<String>, Error> {
    if !parent.is_dir() {
        return Ok(Vec::new());
    }
    let mut ids = Vec::new();
    for entry in fs::read_dir(parent)? {
        let name = entry?.file_name();
        if let Some(name) = name
            .to_str()
            .filter(|name| parse_conversation_id(name).is_ok())
        {
            ids.push(name.to_string());
        }
    }
    Ok(ids)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_query_is_lowercased_terms() {
        assert_eq!(
            parse_query(" The  TTL\u{1f}is ").unwrap(),
            ["the", "ttl", "is"]
        );
        assert_eq!(
            parse_query("  "),
            Err("query must contain at least one term".to_string())
        );
        assert!(match_terms(
            "The cache TTL is 86400",
            &parse_query("ttl CACHE").unwrap()
        ));
        assert!(!match_terms("The cache", &parse_query("ttl").unwrap()));
        assert!(prefilterable("ttl-1") && !prefilterable("\"q\"") && !prefilterable("é"));
        assert!(!prefilterable("a\tb"));
    }
}
