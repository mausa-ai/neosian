//! JSON with comments and trailing commas, as OpenCode reads its
//! `opencode.jsonc` (DESIGN §40.53, the confirming pass's ruling): the text
//! with each comment and trailing comma blanked to spaces, so every byte
//! offset stays where it was and an edit made on the blanked text lands on
//! the same bytes of the original.

/// The byte ranges of the text's comments, outside strings. A line comment
/// ends at a line feed or a bare carriage return, as OpenCode's parser ends
/// it; the line end is not part of the comment.
pub fn spans(text: &str) -> Vec<(usize, usize)> {
    let bytes = text.as_bytes();
    let mut found = vec![];
    let (mut i, mut string, mut escaped) = (0, false, false);
    while i < bytes.len() {
        let b = bytes[i];
        if string {
            match (escaped, b) {
                (true, _) => escaped = false,
                (false, b'\\') => escaped = true,
                (false, b'"') => string = false,
                _ => {}
            }
            i += 1;
            continue;
        }
        let end = match (b, bytes.get(i + 1)) {
            (b'/', Some(b'/')) => text[i..].find(['\n', '\r']).map_or(bytes.len(), |e| i + e),
            (b'/', Some(b'*')) => text[i + 2..]
                .find("*/")
                .map_or(bytes.len(), |e| i + 2 + e + 2),
            _ => {
                string = b == b'"';
                i += 1;
                continue;
            }
        };
        found.push((i, end));
        i = end;
    }
    found
}

/// The text with comments and trailing commas replaced by spaces (newlines
/// kept), the same length in bytes.
pub fn blank(text: &str) -> String {
    let mut out = text.as_bytes().to_vec();
    for (start, end) in spans(text) {
        for slot in &mut out[start..end] {
            if *slot != b'\n' {
                *slot = b' ';
            }
        }
    }
    let (mut string, mut escaped) = (false, false);
    for i in 0..out.len() {
        let b = out[i];
        if string {
            match (escaped, b) {
                (true, _) => escaped = false,
                (false, b'\\') => escaped = true,
                (false, b'"') => string = false,
                _ => {}
            }
        } else if b == b'"' {
            string = true;
        } else if b == b',' {
            let next = out[i + 1..].iter().find(|c| !c.is_ascii_whitespace());
            if matches!(next, Some(b'}' | b']')) {
                out[i] = b' ';
            }
        }
    }
    // Every byte of a comment, and each trailing comma, became an ASCII
    // space; the rest is the original UTF-8.
    String::from_utf8(out).unwrap_or_default()
}

/// The comments of the text, in order (an edit must keep every one).
pub fn comments(text: &str) -> Vec<&str> {
    spans(text).into_iter().map(|(s, e)| &text[s..e]).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn comments_and_trailing_commas_blank_to_spaces_and_strings_stay() {
        let text = "{\n  // a \"x\" comment \u{e9}\n  /* b */ \"k\": \"/* not */ // not\",\n  \"l\": [1, 2, // c\n],\n}\n";
        let blanked = blank(text);
        assert_eq!(blanked.len(), text.len());
        let value: serde_json::Value = serde_json::from_str(&blanked).unwrap();
        assert_eq!(value["k"], "/* not */ // not");
        assert_eq!(value["l"], serde_json::json!([1, 2]));
        assert_eq!(
            comments(text),
            ["// a \"x\" comment \u{e9}", "/* b */", "// c"]
        );
    }

    #[test]
    fn a_bare_carriage_return_ends_a_line_comment() {
        let text = "{\"a\": 1, // c\r \"k\": 2}";
        let value: serde_json::Value = serde_json::from_str(&blank(text)).unwrap();
        assert_eq!(value["k"], 2);
        assert_eq!(comments(text), ["// c"]);
    }
}
