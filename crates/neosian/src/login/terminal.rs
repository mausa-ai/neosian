//! Text for a terminal: what a host sent is cleaned before it is shown, and
//! lines wrap by display width without breaking a word.

/// `text` as it may be shown: escape sequences, control characters and
/// invisible or bidirectional formatting characters removed, a tab read as
/// a space, at most `max` characters (then `...`). Newlines stay only when
/// `lines` is true.
pub fn clean(text: &str, max: usize, lines: bool) -> String {
    let mut shown = String::new();
    let mut count = 0;
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' {
            skip_escape(&mut chars);
            continue;
        }
        let c = match c {
            '\n' if lines => '\n',
            '\t' => ' ',
            c if c.is_control() || hidden(c) => continue,
            c => c,
        };
        if count == max {
            shown.push_str("...");
            break;
        }
        shown.push(c);
        count += 1;
    }
    shown
}

/// The rest of an escape sequence after ESC: CSI up to its final byte, a
/// string sequence (OSC, DCS, SOS, PM, APC) up to BEL or ST, else one character.
fn skip_escape(chars: &mut std::iter::Peekable<std::str::Chars<'_>>) {
    match chars.next() {
        Some('[') => {
            for c in chars.by_ref() {
                if ('\u{40}'..='\u{7e}').contains(&c) {
                    break;
                }
            }
        }
        Some(']' | 'P' | 'X' | '^' | '_') => {
            while let Some(c) = chars.next() {
                if c == '\u{7}' {
                    break;
                }
                if c == '\u{1b}' {
                    chars.next_if_eq(&'\\');
                    break;
                }
            }
        }
        _ => {}
    }
}

/// Characters that change how text around them reads without showing.
fn hidden(c: char) -> bool {
    matches!(c,
        '\u{61c}' | '\u{200b}'..='\u{200f}' | '\u{202a}'..='\u{202e}'
        | '\u{2060}'..='\u{2064}' | '\u{2066}'..='\u{2069}' | '\u{feff}')
}

/// Columns a terminal gives `text`: wide East Asian characters and emoji
/// take two, combining marks none.
pub fn width(text: &str) -> usize {
    text.chars().map(columns).sum()
}

fn columns(c: char) -> usize {
    match u32::from(c) {
        0x300..=0x36f
        | 0x1ab0..=0x1aff
        | 0x1dc0..=0x1dff
        | 0x20d0..=0x20ff
        | 0xfe00..=0xfe0f
        | 0xfe20..=0xfe2f
        | 0xe0100..=0xe01ef => 0,
        0x1100..=0x115f
        | 0x2e80..=0x303e
        | 0x3041..=0x33ff
        | 0x3400..=0x4dbf
        | 0x4e00..=0x9fff
        | 0xa000..=0xa4cf
        | 0xac00..=0xd7a3
        | 0xf900..=0xfaff
        | 0xfe30..=0xfe4f
        | 0xff00..=0xff60
        | 0xffe0..=0xffe6
        | 0x1f300..=0x1f64f
        | 0x1f900..=0x1f9ff
        | 0x20000..=0x3fffd => 2,
        _ => 1,
    }
}

/// `text` in lines of at most `limit` columns, spaces collapsed. A word
/// wider than `limit` (a URL, a path) stays whole on a line of its own, so
/// it can still be copied.
pub fn wrap(text: &str, limit: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut line = String::new();
    let mut used = 0;
    for word in text.split_whitespace() {
        let size = width(word);
        if used > 0 && used + 1 + size > limit {
            lines.push(std::mem::take(&mut line));
            used = 0;
        }
        if used > 0 {
            line.push(' ');
            used += 1;
        }
        line.push_str(word);
        used += size;
    }
    if !line.is_empty() || lines.is_empty() {
        lines.push(line);
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escape_sequences_and_hidden_characters_never_reach_the_terminal() {
        let host = "ok\u{1b}[31m red\u{1b}[0m \u{1b}]8;;https://evil.test\u{7}link\u{1b}]8;;\u{1b}\\ \u{1b}c\u{9b}2J\u{202e}gnp.exe\u{7}\r\tend";
        assert_eq!(clean(host, 200, false), "ok red link 2Jgnp.exe end");
        assert_eq!(clean("one\ntwo\u{1b}[2K", 200, true), "one\ntwo");
        assert_eq!(clean("one\ntwo", 200, false), "onetwo");
    }

    #[test]
    fn host_text_is_bounded() {
        assert_eq!(clean("abcdef", 3, false), "abc...");
        assert_eq!(clean("abc", 3, false), "abc");
        assert_eq!(clean("\u{1b}[1mab", 2, false), "ab");
    }

    #[test]
    fn unicode_width_counts_columns_not_bytes() {
        assert_eq!(width("abc"), 3);
        assert_eq!(width("日本語"), 6);
        assert_eq!(width("e\u{301}"), 1);
        assert_eq!(width("café"), 4);
    }

    #[test]
    fn wrapping_keeps_words_and_long_urls_whole() {
        assert_eq!(wrap("one two three four", 9), ["one two", "three", "four"]);
        let url = "https://cloud.neosian.com/connect?user_code=ABCD-EFGH";
        assert_eq!(wrap(&format!("open {url} now"), 20), ["open", url, "now"]);
        assert_eq!(wrap("日本語 日本語", 8), ["日本語", "日本語"]);
        assert_eq!(wrap("", 10), [""]);
    }
}
