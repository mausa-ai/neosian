//! History search (`conversation/search.py`, `file_search.py`, DESIGN
//! §32): the one rule every substrate applies, and the file scan that
//! applies it here. A query is whitespace-split into lowercased terms; a
//! turn matches when every term is a substring of its folded searchable
//! text; hits come newest first under one total order; `rank.rs` adds
//! history ranking version 1. No wildcards, no stemming.

use std::fs;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::Path;

use super::ids::parse_conversation_id;
use super::message::{Message, text_of};
use super::rows::parse_turn;
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

/// Whether the file holds the term verbatim when a turn holds it:
/// printable ASCII free of `"` and `\`, everything else being escaped
/// by `json.dumps(ensure_ascii=True)`.
pub fn prefilterable(term: &str) -> bool {
    term.bytes()
        .all(|byte| (b' '..=b'~').contains(&byte) && byte != b'"' && byte != b'\\')
}

/// One log's candidate turns, streamed a line at a time (DESIGN §40.45): a
/// line is decoded when its raw text could hold every term (`all`, version 1
/// and newest) or at least one term (relevance). A term the raw line cannot
/// hold verbatim forces decoding. A malformed line lacking a prefilterable
/// term is invisible here, while a candidate malformed line is refused
/// exactly as a read refuses it. `visit` gets the turn, the byte offset where
/// its line starts and its line number; no line outlives its visit.
pub fn scan_log(
    file: &Path,
    conversation_id: &str,
    terms: &[String],
    all: bool,
    mut visit: impl FnMut(Turn, u64, usize) -> Result<(), Error>,
) -> Result<(), Error> {
    let raw: Vec<&str> = terms
        .iter()
        .map(String::as_str)
        .filter(|term| prefilterable(term))
        .collect();
    let every = raw.len() == terms.len();
    let mut reader = BufReader::new(fs::File::open(file)?);
    let mut buffer = String::new();
    let (mut offset, mut number) = (0_u64, 0_usize);
    loop {
        buffer.clear();
        let read = reader.read_line(&mut buffer)?;
        if read == 0 {
            return Ok(());
        }
        let start = offset;
        offset += read as u64;
        number += 1;
        let line = line_text(&buffer);
        let lowered = line.to_lowercase();
        let candidate = if all {
            raw.iter().all(|term| lowered.contains(term))
        } else {
            !every || raw.iter().any(|term| lowered.contains(term))
        };
        if candidate {
            visit(parse_turn(line, number, conversation_id)?, start, number)?;
        }
    }
}

/// The turn whose log line starts at `offset`, read again under the same lock.
pub fn read_at(
    reader: &mut BufReader<fs::File>,
    offset: u64,
    number: usize,
    conversation_id: &str,
) -> Result<Turn, Error> {
    reader.seek(SeekFrom::Start(offset))?;
    let mut buffer = String::new();
    reader.read_line(&mut buffer)?;
    Ok(parse_turn(line_text(&buffer), number, conversation_id)?)
}

/// A line without its ending, as `str::lines` yields it: `\n`, then `\r`.
pub fn line_text(buffer: &str) -> &str {
    match buffer.strip_suffix('\n') {
        Some(line) => line.strip_suffix('\r').unwrap_or(line),
        None => buffer,
    }
}

/// Every entry under `parent` with a grammatical name; a foreign name is
/// skipped, as listings skip foreign files.
pub fn held_ids(parent: &Path) -> Result<Vec<String>, Error> {
    if !crate::store::fileio::within(parent.parent().unwrap_or(parent), parent)? {
        return Err(super::Refused::id_invalid("conversations", "escapes the store root").into());
    }
    if !parent.is_dir() {
        return Ok(Vec::new());
    }
    let mut ids = Vec::new();
    for entry in fs::read_dir(parent)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let name = entry.file_name();
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
