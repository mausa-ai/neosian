//! The split rule (§40.54 item 3.4): chunk lengths worked out by hand for
//! each way a cut is chosen, and the rule restated over generated texts.

use super::generate::{self, Rng};
use crate::conversation::turn_records::{TEXT_BYTES, WINDOW_BYTES, encoded_len, split};
use crate::text::is_space;

fn lengths(text: &str) -> Vec<usize> {
    let chunks = split(text);
    assert_eq!(chunks.concat(), text);
    chunks.iter().map(|chunk| chunk.len()).collect()
}

#[test]
fn encoded_lengths_count_quotes_escapes_and_raw_bytes() {
    assert_eq!(encoded_len(""), 2);
    assert_eq!(encoded_len("ab"), 4);
    assert_eq!(encoded_len("\"\\\n\r\t\u{8}\u{c}"), 2 + 7 * 2);
    assert_eq!(encoded_len("\u{0}\u{1}\u{1f}"), 2 + 3 * 6);
    assert_eq!(encoded_len("\u{7f}é中😀"), 2 + 1 + 2 + 3 + 4);
}

/// Each case's lengths in UTF-8 bytes, worked out by hand: 65,534 bytes
/// of plain text plus the quotes fill one record exactly.
#[test]
fn every_kind_of_cut_lands_where_the_rule_says() {
    let a = |n: usize| "a".repeat(n);
    let cases: Vec<(&str, String, Vec<usize>)> = vec![
        ("empty", String::new(), vec![0]),
        ("exactly full", a(65_534), vec![65_534]),
        ("one byte over, forced", a(65_535), vec![65_534, 1]),
        ("forced", "b".repeat(70_000), vec![65_534, 4_466]),
        (
            "three chunks",
            "e".repeat(140_000),
            vec![65_534, 65_534, 8_932],
        ),
        (
            "after the last space",
            format!("{} ", "c".repeat(99)).repeat(700),
            vec![65_500, 4_500],
        ),
        (
            "a line feed before a later space",
            format!(
                "{}\n{} {}",
                "x".repeat(62_000),
                "y".repeat(1_000),
                "z".repeat(10_000)
            ),
            vec![62_001, 11_001],
        ),
        (
            "a line feed outside the last 4,096 bytes",
            format!("{}\n{}", "x".repeat(60_000), "y".repeat(10_000)),
            vec![65_533, 4_468],
        ),
        (
            "a separator counts as space",
            format!("{}\u{1f}", "d".repeat(50)).repeat(2_000),
            vec![59_670, 42_330],
        ),
        (
            "a line separator is space",
            format!("{}\u{2028}{}", a(65_000), "b".repeat(5_000)),
            vec![65_003, 5_000],
        ),
        (
            "control characters six bytes each",
            "\u{1}".repeat(20_000),
            vec![10_922, 9_078],
        ),
        (
            "two-byte characters",
            "é".repeat(40_000),
            vec![65_534, 14_466],
        ),
        (
            "a four-byte character never split",
            format!("x{}", "😀".repeat(20_000)),
            vec![65_533, 14_468],
        ),
        (
            "an escape that does not fit",
            format!("{}\n", "f".repeat(65_533)),
            vec![65_533, 1],
        ),
    ];
    for (case, text, expected) in cases {
        assert_eq!(lengths(&text), expected, "{case}");
    }
}

/// The rule restated: for each chunk but the last, the longest prefix of
/// what is left that fits, cut after its last line feed within its last
/// 4,096 bytes, else after its last white space there, else at its end.
fn follows_the_rule(text: &str, chunks: &[&str]) {
    assert_eq!(chunks.concat(), text);
    let mut rest = text;
    for (at, chunk) in chunks.iter().enumerate() {
        assert!(encoded_len(chunk) <= TEXT_BYTES);
        if at + 1 == chunks.len() {
            assert!(
                encoded_len(rest) <= TEXT_BYTES,
                "the last chunk is what is left"
            );
            assert_eq!(*chunk, rest);
            return;
        }
        assert!(
            encoded_len(rest) > TEXT_BYTES,
            "a chunk is cut only from what does not fit"
        );
        let chars: Vec<(usize, char)> = rest.char_indices().collect();
        let (mut fitting, mut used) = (0, 2);
        while fitting < chars.len() {
            used += encoded_len(&chars[fitting].1.to_string()) - 2;
            if used > TEXT_BYTES {
                break;
            }
            fitting += 1;
        }
        let end = chars[fitting - 1].0 + chars[fitting - 1].1.len_utf8();
        let window: Vec<&(usize, char)> = chars[..fitting]
            .iter()
            .filter(|(byte, _)| *byte >= end.saturating_sub(WINDOW_BYTES))
            .collect();
        let after = |found: Option<&&(usize, char)>| found.map(|(byte, ch)| byte + ch.len_utf8());
        let cut = after(window.iter().rev().find(|(_, ch)| *ch == '\n'))
            .or_else(|| after(window.iter().rev().find(|(_, ch)| is_space(*ch))))
            .unwrap_or(end);
        assert_eq!(chunk.len(), cut, "chunk {at}");
        rest = &rest[cut..];
    }
}

#[test]
fn generated_texts_are_cut_by_the_rule() {
    for seed in 0..40 {
        let mut rng = Rng::new(20_000 + seed);
        let text = generate::long_text(&mut rng);
        follows_the_rule(&text, &split(&text));
    }
}
