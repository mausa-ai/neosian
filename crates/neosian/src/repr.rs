//! Python's `repr` of a string: every `{value!r}` in a message the two
//! doors share is spelled with it, so the text stays byte-equal.

/// Single quotes, or double quotes when the text holds a single quote and
/// no double; the quote, the backslash and the control characters escaped.
pub fn repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            ch if ch == quote => out.extend(['\\', ch]),
            ch if ch < ' ' || ch == '\x7f' => out.push_str(&format!("\\x{:02x}", ch as u32)),
            ch => out.push(ch),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::repr;

    #[test]
    fn repr_is_pythons() {
        assert_eq!(repr("nope"), "'nope'");
        assert_eq!(repr("it's"), "\"it's\"");
        assert_eq!(repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(repr("a\\b\n\x01"), "'a\\\\b\\n\\x01'");
    }
}
