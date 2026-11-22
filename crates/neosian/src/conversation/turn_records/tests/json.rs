//! The JSON check (§40.54 item 9, T1b1a note and the review's
//! corrections): what it reads, split at every place a chunk can end; what
//! it refuses and why; and over generated values and mutated texts, never
//! accepting what serde_json refuses.

use serde_json::Value;

use super::generate::{self, Rng};
use crate::conversation::turn_records::{JsonCheck, Kind, Shape, depth};
use crate::pyfmt::dumps;

/// The text fed at every split into two chunks, and whole.
fn read(shape: Shape, text: &str) -> Result<(), String> {
    let whole = {
        let mut check = JsonCheck::new(shape, DEPTH);
        check.feed(text).and_then(|()| check.finish())
    };
    for cut in (0..=text.len()).filter(|at| text.is_char_boundary(*at)) {
        let mut check = JsonCheck::new(shape, DEPTH);
        let split = check
            .feed(&text[..cut])
            .and_then(|()| check.feed(&text[cut..]));
        assert_eq!(
            split.and_then(|()| check.finish()),
            whole,
            "{text:?} cut at {cut}"
        );
    }
    whole.map(|_| ())
}

/// A reasoning's bound: 124.
const DEPTH: usize = 124;

#[test]
fn compact_values_read_however_they_are_cut() {
    for text in [
        "null",
        "true",
        "false",
        "0",
        "-0.0",
        "12",
        "-3.25e-07",
        "1e+300",
        "\"\"",
        "\"a\\\"b\\\\c/\\b\\f\\n\\r\\t\\u0001\"",
        "\"é中😀\"",
        "[]",
        "{}",
        "[1,[2,{\"a\":[null]}],\"x\"]",
        "{\"command\":\"ls -l\",\"b\":[1,2.5,true,null]}",
        "1.2345678901234568e+29",
    ] {
        assert_eq!(read(Shape::Any, text), Ok(()), "{text}");
    }
}

#[test]
fn anything_but_compact_json_is_refused() {
    for (text, why) in [
        ("", "ends early"),
        (" 1", "cannot start a value"),
        ("[1, 2]", "cannot start a value"),
        ("{\"a\": 1}", "cannot start a value"),
        ("[1,]", "out of place"),
        ("{\"a\":1,}", "out of place"),
        ("01", "out of place"),
        ("1.", "ends early"),
        ("-", "ends early"),
        ("1e400", "out of range"),
        ("tru", "ends early"),
        ("nul1", "unknown word"),
        ("\"a", "ends early"),
        ("\"\u{1}\"", "raw control character"),
        ("\"\\x\"", "unknown escape"),
        ("\"\\u12g4\"", "short \\u escape"),
        ("\"\\ud800\"", "an escape the codec does not write"),
        ("\"\\/\"", "an escape the codec does not write"),
        ("\"\\u00e9\"", "an escape the codec does not write"),
        ("-0", "not spelled as the codec writes it"),
        ("1E+300", "not spelled as the codec writes it"),
        ("123456789012345678901234567890", "not spelled"),
        ("{\"a\":1,\"a\":1}", "a key repeated in its object"),
        ("[1]]", "out of place"),
        ("1 ", "out of place"),
        ("{1:2}", "out of place"),
    ] {
        let refused = read(Shape::Any, text).expect_err(text);
        assert!(refused.contains(why), "{text:?}: {refused}");
    }
    assert_eq!(depth(Kind::Reasoning, None), DEPTH);
    let deep = format!("{}{}", "[".repeat(DEPTH), "]".repeat(DEPTH));
    assert_eq!(read(Shape::Any, &deep), Ok(()));
    let deeper = format!("{}{}", "[".repeat(DEPTH + 1), "]".repeat(DEPTH + 1));
    assert!(
        read(Shape::Any, &deeper)
            .unwrap_err()
            .contains("deeper than 124")
    );
    assert!(
        read(Shape::Any, &format!("{}1", "9".repeat(1_100)))
            .unwrap_err()
            .contains("longer than 1,100")
    );
}

#[test]
fn arguments_are_an_object_and_an_attachment_a_block_by_the_codec_rules() {
    assert_eq!(read(Shape::Object, "{\"a\":[1]}"), Ok(()));
    for text in ["[]", "1", "\"x\"", "null"] {
        assert_eq!(
            read(Shape::Object, text),
            Err("arguments must be an object".into())
        );
    }
    for text in [
        r#"{"type":"image","media_type":"image/png","data":"iVBO","url":null}"#,
        r#"{"type":"document","media_type":null,"data":null,"url":"https://x/y.pdf"}"#,
        r#"{"type":"compaction","content":{"a":[1]},"encrypted_content":null}"#,
    ] {
        assert_eq!(read(Shape::Block, text), Ok(()), "{text}");
    }
    for (text, why) in [
        ("[1]", "not a content block"),
        (r#"{"type":"text","text":"t"}"#, "a block other than text"),
        (
            r#"{"type":"video"}"#,
            "Unknown content block type: \"video\"",
        ),
        (r#"{"url":"u"}"#, "not laid out as the codec writes it"),
        (r#"{"url":"u","type":"image"}"#, "not laid out"),
        (r#"{"type":"document","url":"u"}"#, "not laid out"),
        (
            r#"{"type":"document","media_type":null,"data":null,"url":"u","junk":1}"#,
            "not laid out",
        ),
        (
            r#"{"type":"image","media_type":"m","data":"iVBO Rw0K","url":null}"#,
            "not laid out",
        ),
        (r#"{"type":"compaction","content":1}"#, "not laid out"),
        (r#"{"type":7,"url":"u"}"#, "Unknown content block type"),
        (
            r#"{"type":"image","media_type":"m","data":"d","url":"u"}"#,
            "exactly one of 'data' or 'url'",
        ),
        (r#"{"type":"image"}"#, "exactly one of 'data' or 'url'"),
        (
            r#"{"type":"image","media_type":null,"data":"d","url":null}"#,
            "media_type with base64 data",
        ),
        (
            r#"{"type":"document","media_type":null,"data":null,"url":5}"#,
            "DocumentBlock url must be a string",
        ),
        (
            r#"{"type":"image","media_type":[],"data":null,"url":"u"}"#,
            "ImageBlock media_type must be a string",
        ),
        (
            r#"{"typeXXXXXXXXXXXXXXXXXXXXXXXXXX":"image","url":"u"}"#,
            "not laid out",
        ),
    ] {
        let refused = read(Shape::Block, text).expect_err(text);
        assert!(refused.contains(why), "{text}: {refused}");
    }
}

#[test]
fn the_check_never_accepts_what_serde_json_refuses() {
    let mut rng = Rng::new(50_000);
    let mut accepted = 0;
    for _ in 0..3_000 {
        let value = generate::json(&mut rng, 4);
        let text = dumps(&value, false);
        let mut check = JsonCheck::new(Shape::Any, DEPTH);
        let cut = (0..=text.len())
            .filter(|at| text.is_char_boundary(*at))
            .nth(rng.below(text.len() as u64 + 1) as usize)
            .unwrap_or(0);
        check.feed(&text[..cut]).unwrap();
        check.feed(&text[cut..]).unwrap();
        check.finish().unwrap_or_else(|why| panic!("{text}: {why}"));
        let at = rng.below(text.len() as u64 + 1) as usize;
        let at = (at..=text.len())
            .find(|at| text.is_char_boundary(*at))
            .unwrap();
        let mutated = format!(
            "{}{}{}",
            &text[..at],
            generate::text(&mut rng, 2),
            &text[at..]
        );
        let mut check = JsonCheck::new(Shape::Any, DEPTH);
        if check.feed(&mutated).and_then(|()| check.finish()).is_ok() {
            accepted += 1;
            assert!(
                serde_json::from_str::<Value>(&mutated).is_ok(),
                "{mutated:?}"
            );
        }
    }
    assert!(accepted > 100, "{accepted}");
}
