//! Python's text rules where the two doors must agree character for
//! character (DESIGN §39.3): `str.isspace`, `split`, `splitlines`, `strip`
//! and `len`, each of which differs from Rust's nearest method on some
//! character.

/// `str.isspace`: Unicode White_Space, plus the four separators U+001C to
/// U+001F that Python counts and Unicode does not.
pub fn is_space(ch: char) -> bool {
    ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch)
}

/// `" ".join(text.split())`: runs of whitespace collapsed to one space,
/// the ends trimmed.
pub fn flatten(text: &str) -> String {
    text.split(is_space)
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// `str.splitlines()`: Python's line boundaries, `\r\n` one of them, no
/// empty line after the last boundary.
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((at, ch)) = chars.next() {
        let mut after = at + ch.len_utf8();
        match ch {
            '\r' => {
                if chars.peek().is_some_and(|(_, next)| *next == '\n') {
                    chars.next();
                    after += 1;
                }
            }
            '\n' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => {}
            _ => continue,
        }
        lines.push(&text[start..at]);
        start = after;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `str.rstrip()`.
pub fn rstrip(text: &str) -> &str {
    text.trim_end_matches(is_space)
}

/// `str.lstrip()`.
pub fn lstrip(text: &str) -> &str {
    text.trim_start_matches(is_space)
}

/// Python's `len`: characters, never bytes.
pub fn len(text: &str) -> usize {
    text.chars().count()
}

/// `text[..at]` by character index.
pub fn head(text: &str, at: usize) -> &str {
    let end = text
        .char_indices()
        .nth(at)
        .map_or(text.len(), |(byte, _)| byte);
    &text[..end]
}

/// `text[at..]` by character index.
pub fn tail(text: &str, at: usize) -> &str {
    let start = text
        .char_indices()
        .nth(at)
        .map_or(text.len(), |(byte, _)| byte);
    &text[start..]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn whitespace_is_pythons() {
        assert!(is_space('\u{1c}') && is_space('\u{a0}') && is_space('\n'));
        assert!(!is_space('x') && !is_space('\u{200b}'));
        assert_eq!(flatten("  a \u{1f}b\n\n c  "), "a b c");
        assert_eq!(strip(" \u{1d}x\u{85}"), "x");
        assert_eq!((rstrip("x \n"), lstrip("\t x ")), ("x", "x "));
    }

    #[test]
    fn splitlines_is_pythons() {
        assert_eq!(splitlines("a\r\nb\rc\nd\n"), ["a", "b", "c", "d"]);
        assert_eq!(splitlines("a\u{2028}b\u{c}c"), ["a", "b", "c"]);
        assert_eq!(splitlines(""), Vec::<&str>::new());
        assert_eq!(splitlines("\n\n"), ["", ""]);
        assert_eq!(splitlines("one"), ["one"]);
    }

    #[test]
    fn indices_count_characters() {
        assert_eq!(len("café ✓"), 6);
        assert_eq!(head("café ✓", 4), "café");
        assert_eq!(tail("café ✓", 5), "✓");
        assert_eq!((head("ab", 9), tail("ab", 9)), ("ab", ""));
    }
}
