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
use super::rows_header::{Row, parse_row};
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
/// exactly as a read refuses it. Where the store reads format 2 (`records`),
/// a header row holds no message text, so it is always a candidate (DESIGN
/// §40.54 item 15.1); elsewhere a format-2 row is newer than supported, as
/// before. `visit` gets the row, the byte offset where its line starts and
/// its line number; no line outlives its visit.
pub fn scan_log(
    file: &Path,
    conversation_id: &str,
    terms: &[String],
    all: bool,
    records: bool,
    mut visit: impl FnMut(Row, u64, usize) -> Result<(), Error>,
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
        let header = records && line.starts_with(HEADER);
        let lowered = line.to_lowercase();
        let candidate = header
            || if all {
                raw.iter().all(|term| holds(&lowered, term))
            } else {
                !every || raw.iter().any(|term| holds(&lowered, term))
            };
        if candidate {
            visit(
                parse_row(line, number, conversation_id, records)?,
                start,
                number,
            )?;
        }
    }
}

/// Whether a lowercased raw line can hold `term` once decoded: verbatim,
/// or through the escape of one of the two characters past ASCII whose
/// lowercase holds ASCII, U+0130 (`i` and a combining dot) and U+212A
/// KELVIN SIGN (`k`), which a line spells `\u0130` and `\u212a` (found
/// while building T1b2a2: the prefilter passed over such a turn).
fn holds(line: &str, term: &str) -> bool {
    line.contains(term)
        || (term.contains('i') && line.contains("\\u0130"))
        || (term.contains('k') && line.contains("\\u212a"))
}

/// How a header row begins: `render_header` writes `neosian_format` first.
const HEADER: &str = "{\"neosian_format\":2,";

/// The row whose log line starts at `offset`, read again under the same lock.
pub fn read_at(
    reader: &mut BufReader<fs::File>,
    offset: u64,
    number: usize,
    conversation_id: &str,
    records: bool,
) -> Result<Row, Error> {
    reader.seek(SeekFrom::Start(offset))?;
    let mut buffer = String::new();
    reader.read_line(&mut buffer)?;
    Ok(parse_row(
        line_text(&buffer),
        number,
        conversation_id,
        records,
    )?)
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
    let mut ids = Vec::new();
    visit_ids(parent, |name| {
        ids.push(name.to_owned());
        Ok(())
    })?;
    Ok(ids)
}

/// Visit grammatical directory names without retaining a store-wide listing.
pub fn visit_ids(
    parent: &Path,
    mut visit: impl FnMut(&str) -> Result<(), Error>,
) -> Result<(), Error> {
    if !crate::store::fileio::within(parent.parent().unwrap_or(parent), parent)? {
        return Err(super::Refused::id_invalid("conversations", "escapes the store root").into());
    }
    if !parent.is_dir() {
        return Ok(());
    }
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
            visit(name)?;
        }
    }
    Ok(())
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

    /// Found while building T1b2a2 (DESIGN §40.54): a format-1 line spells
    /// non-ASCII as `\uXXXX`, and two such characters lowercase to text
    /// holding ASCII (U+0130 to `i` and a combining dot, U+212A KELVIN SIGN
    /// to `k`), so the raw-line prefilter passed over a turn that matches.
    /// No other character does, for the Unicode tables in use.
    #[test]
    fn the_prefilter_passes_no_turn_a_term_matches() {
        let lowered: Vec<u32> = (0x80..=0x10_ffff_u32)
            .filter_map(char::from_u32)
            .filter(|ch| ch.to_lowercase().any(|low| low.is_ascii()))
            .map(u32::from)
            .collect();
        assert_eq!(lowered, [0x130, 0x212a]);
        let home = crate::testing::Scratch::new("search-prefilter");
        for (text, term) in [("SAM\u{130}", "sami"), ("5 \u{212a}ELVIN", "kelvin")] {
            let turn = Turn {
                conversation_id: "c-1".into(),
                turn: 1,
                messages: vec![
                    crate::conversation::message::from_json(
                        &serde_json::json!({"role": "user", "content": text}),
                    )
                    .unwrap(),
                ],
                created_at: crate::store::time::Stamp::now(),
                actor: None,
                redacted: false,
            };
            home.plant(
                "turns.jsonl",
                &crate::conversation::rows::render_turn(&turn),
            );
            let file = home.path().join("turns.jsonl");
            assert!(match_terms(&turn_text(&turn), &[term.to_string()]));
            for all in [true, false] {
                let mut seen = 0;
                scan_log(&file, "c-1", &[term.to_string()], all, false, |_, _, _| {
                    seen += 1;
                    Ok(())
                })
                .unwrap();
                assert_eq!(seen, 1, "{text:?} holds {term:?}");
            }
        }
    }
}
