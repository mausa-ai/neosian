//! A record's size and the split rule (§40.54 item 3.4): the bound is on
//! the text as the wire encodes it, and a longer piece is cut by one rule
//! so that every binary turns the same message into the same records.

use super::{TEXT_BYTES, WINDOW_BYTES};
use crate::text::is_space;

/// One character's bytes in a compact JSON string with non-ASCII raw:
/// the two-character escapes, `\u00XX` for the other control characters.
pub(super) fn width(ch: char) -> usize {
    match ch {
        '"' | '\\' | '\n' | '\r' | '\t' | '\u{8}' | '\u{c}' => 2,
        ch if ch < ' ' => 6,
        ch => ch.len_utf8(),
    }
}

/// `text` as a JSON string encodes it, quotes included.
pub fn encoded_len(text: &str) -> usize {
    2 + text.chars().map(width).sum::<usize>()
}

/// The chunks of one piece, in order; their concatenation is `text`. A
/// piece that fits is one chunk (an empty piece one empty chunk). Else:
/// the longest prefix that fits, cut just after the last line feed in its
/// last 4,096 bytes, else just after the last white space there (the query
/// splitter's), else where it ends (a forced cut); then the rest likewise.
pub fn split(text: &str) -> Vec<&str> {
    let mut chunks = Vec::new();
    let mut rest = text;
    let mut left = encoded_len(text);
    while left > TEXT_BYTES {
        let (mut used, mut end) = (2, 0);
        for (at, ch) in rest.char_indices() {
            if used + width(ch) > TEXT_BYTES {
                break;
            }
            used += width(ch);
            end = at + ch.len_utf8();
        }
        let mut start = end.saturating_sub(WINDOW_BYTES);
        while !rest.is_char_boundary(start) {
            start += 1;
        }
        let window = &rest[start..end];
        let cut = match window.rfind('\n') {
            Some(at) => start + at + 1,
            None => window
                .char_indices()
                .rev()
                .find(|(_, ch)| is_space(*ch))
                .map_or(end, |(at, ch)| start + at + ch.len_utf8()),
        };
        let (chunk, after) = rest.split_at(cut);
        left -= encoded_len(chunk) - 2;
        chunks.push(chunk);
        rest = after;
    }
    chunks.push(rest);
    chunks
}
