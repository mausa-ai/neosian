//! One YAML scalar as PyYAML reads and writes it (`yaml.safe_load` and
//! `yaml.safe_dump`, YAML 1.1): the typing of a plain word, the two quoted
//! forms, and the spelling `safe_dump` chooses for a JSON scalar. The
//! envelope's unknown keys cross the wire as JSON through `read`, a
//! restore's `extra` lands as YAML through `spell` (DESIGN §18.2, §39.11).
//! JSON collections also round-trip on one line. Other YAML collections
//! require the explicit v1 metadata migration before crossing the wire.

use serde_json::{Number, Value};

use crate::pyfmt::float_repr;

const NULL: [&str; 4] = ["~", "null", "Null", "NULL"];
const TRUE: [&str; 9] = [
    "true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON",
];
const FALSE: [&str; 9] = [
    "false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF",
];

/// A mapping line's separator is outside its quoted key. A colon inside
/// JSON or YAML quotes belongs to the key, including escaped quotes.
pub fn key_value(line: &str) -> Option<(&str, &str)> {
    let mut quote = None;
    let mut chars = line.char_indices().peekable();
    while let Some((at, ch)) = chars.next() {
        if let Some(held) = quote {
            if held == '"' && ch == '\\' {
                chars.next();
            } else if ch == held {
                if held == '\'' && chars.peek().is_some_and(|(_, next)| *next == '\'') {
                    chars.next();
                } else {
                    quote = None;
                }
            }
        } else if at == 0 && matches!(ch, '\'' | '"') {
            quote = Some(ch);
        } else if ch == ':' {
            let value = &line[at + 1..];
            if value.is_empty() || value.starts_with(char::is_whitespace) {
                return Some((&line[..at], value.trim()));
            }
        }
    }
    None
}

/// PyYAML's reading of one scalar; None for a form this reader does not
/// take (a collection, an unterminated quote, an unknown escape).
pub fn read(raw: &str) -> Option<Value> {
    let text = raw.trim();
    if text.starts_with(['[', '{']) {
        return serde_json::from_str(text).ok();
    }
    if let Some(inner) = text.strip_prefix('\'') {
        return single_quoted(inner);
    }
    if let Some(inner) = text.strip_prefix('"') {
        return double_quoted(inner);
    }
    let plain = match text.find(" #") {
        Some(at) => text[..at].trim_end(),
        None => text,
    };
    if plain.starts_with(['[', '{', '|', '>', '&', '*', '!', '%', '@', '`', '#'])
        || plain == "-"
        || plain.starts_with("- ")
        || plain.contains(": ")
        || plain.ends_with(':')
    {
        return None;
    }
    Some(typed(plain))
}

/// The implicit type of a plain word.
fn typed(plain: &str) -> Value {
    if plain.is_empty() || NULL.contains(&plain) {
        return Value::Null;
    }
    if TRUE.contains(&plain) {
        return Value::Bool(true);
    }
    if FALSE.contains(&plain) {
        return Value::Bool(false);
    }
    if let Some(number) = integer(plain) {
        return Value::Number(number.into());
    }
    if let Some(number) = float(plain).and_then(Number::from_f64) {
        return Value::Number(number);
    }
    Value::String(plain.to_string())
}

/// YAML 1.1's integer forms: decimal, `0x`, `0b`, and a leading `0` as
/// octal, underscores allowed.
fn integer(plain: &str) -> Option<i64> {
    let (negative, digits) = match plain.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, plain.strip_prefix('+').unwrap_or(plain)),
    };
    let digits: String = digits.chars().filter(|ch| *ch != '_').collect();
    let (radix, body) = match digits.strip_prefix("0x") {
        Some(hex) => (16, hex),
        None => match digits.strip_prefix("0b") {
            Some(binary) => (2, binary),
            None if digits.len() > 1 && digits.starts_with('0') => (8, &digits[1..]),
            None => (10, digits.as_str()),
        },
    };
    if body.is_empty() || !body.chars().all(|ch| ch.is_digit(radix)) {
        return None;
    }
    let value = i64::from_str_radix(body, radix).ok()?;
    Some(if negative { -value } else { value })
}

/// YAML 1.1's float: digits around a mandatory `.`, an exponent with a
/// signed integer.
fn float(plain: &str) -> Option<f64> {
    let unsigned = plain.strip_prefix(['-', '+']).unwrap_or(plain);
    let (mantissa, exponent) = match unsigned.find(['e', 'E']) {
        Some(at) => (&unsigned[..at], Some(&unsigned[at + 1..])),
        None => (unsigned, None),
    };
    let (whole, fraction) = mantissa.split_once('.')?;
    let digits = |text: &str| text.chars().all(|ch| ch.is_ascii_digit() || ch == '_');
    if !(digits(whole) && digits(fraction)) || (whole.is_empty() && fraction.is_empty()) {
        return None;
    }
    if let Some(exponent) = exponent {
        let signed = exponent.starts_with(['-', '+']) && exponent.len() > 1;
        if !signed || !exponent[1..].chars().all(|ch| ch.is_ascii_digit()) {
            return None;
        }
    }
    plain.replace('_', "").parse().ok()
}

fn single_quoted(inner: &str) -> Option<Value> {
    let mut text = String::new();
    let mut rest = inner;
    loop {
        let close = rest.find('\'')?;
        text.push_str(&rest[..close]);
        rest = &rest[close + 1..];
        if let Some(after) = rest.strip_prefix('\'') {
            text.push('\'');
            rest = after;
        } else {
            break;
        }
    }
    comment_only(rest).then_some(Value::String(text))
}

fn double_quoted(inner: &str) -> Option<Value> {
    let mut text = String::new();
    let mut chars = inner.chars();
    loop {
        match chars.next()? {
            '"' => break,
            '\\' => {
                let escaped = chars.next()?;
                let hex = |chars: &mut std::str::Chars, width: usize| {
                    let code: String = chars.by_ref().take(width).collect();
                    (code.len() == width)
                        .then(|| u32::from_str_radix(&code, 16).ok())
                        .flatten()
                        .and_then(char::from_u32)
                };
                text.push(match escaped {
                    '0' => '\0',
                    'a' => '\u{7}',
                    'b' => '\u{8}',
                    't' | '\t' => '\t',
                    'n' => '\n',
                    'v' => '\u{b}',
                    'f' => '\u{c}',
                    'r' => '\r',
                    'e' => '\u{1b}',
                    ' ' => ' ',
                    '"' => '"',
                    '/' => '/',
                    '\\' => '\\',
                    'N' => '\u{85}',
                    '_' => '\u{a0}',
                    'L' => '\u{2028}',
                    'P' => '\u{2029}',
                    'x' => hex(&mut chars, 2)?,
                    'u' => hex(&mut chars, 4)?,
                    'U' => hex(&mut chars, 8)?,
                    _ => return None,
                });
            }
            ch => text.push(ch),
        }
    }
    comment_only(chars.as_str()).then_some(Value::String(text))
}

fn comment_only(rest: &str) -> bool {
    let rest = rest.trim();
    rest.is_empty() || rest.starts_with('#')
}

/// YAML scalar spelling; JSON spelling for a list or object.
pub fn spell(value: &Value) -> Option<String> {
    Some(match value {
        Value::Null => "null".to_string(),
        Value::Bool(true) => "true".to_string(),
        Value::Bool(false) => "false".to_string(),
        Value::Number(number) => match number.as_f64() {
            Some(float) if !number.is_i64() && !number.is_u64() => {
                let text = float_repr(float);
                if !text.contains('.') && text.contains('e') {
                    text.replacen('e', ".0e", 1)
                } else {
                    text
                }
            }
            _ => number.to_string(),
        },
        Value::String(text) => spell_str(text),
        Value::Array(_) | Value::Object(_) => value.to_string(),
    })
}

/// Does the emitter's analysis leave a character printable: ASCII, or the
/// Unicode `allow_unicode=True` writes raw?
fn printable(ch: char) -> bool {
    matches!(ch, '\u{20}'..='\u{7e}')
        || (matches!(ch, '\u{a0}'..='\u{d7ff}' | '\u{e000}'..='\u{fffd}' | '\u{10000}'..='\u{10ffff}')
            && ch != '\u{feff}')
}

/// Could the emitter write `text` plain in a block mapping's value?
fn plain_allowed(text: &str) -> bool {
    let chars: Vec<char> = text.chars().collect();
    let Some((&first, &last)) = chars.first().zip(chars.last()) else {
        return false;
    };
    let space = |ch: char| {
        matches!(
            ch,
            '\0' | ' ' | '\t' | '\r' | '\n' | '\u{85}' | '\u{2028}' | '\u{2029}'
        )
    };
    if space(first) || space(last) || !chars.iter().all(|ch| printable(*ch)) {
        return false;
    }
    let followed_by_space = |at: usize| chars.get(at + 1).is_none_or(|ch| space(*ch));
    if "#,[]{}&*!|>'\"%@`".contains(first)
        || (matches!(first, '?' | ':' | '-') && followed_by_space(0))
    {
        return false;
    }
    for (at, ch) in chars.iter().enumerate().skip(1) {
        if (*ch == ':' && followed_by_space(at)) || (*ch == '#' && space(chars[at - 1])) {
            return false;
        }
    }
    matches!(typed(text), Value::String(_))
        && !timestamp_shaped(text)
        && text != "="
        && text != "<<"
}

/// A plain word PyYAML would read as a date or a moment.
fn timestamp_shaped(text: &str) -> bool {
    let bytes = text.as_bytes();
    bytes.len() >= 8
        && bytes[..4].iter().all(u8::is_ascii_digit)
        && bytes[4] == b'-'
        && bytes[5].is_ascii_digit()
        && text[6..]
            .split(['-', 'T', 't', ' '])
            .next()
            .is_some_and(|month| {
                month.bytes().all(|b| b.is_ascii_digit()) && !month.is_empty() && month.len() <= 2
            })
        && text[5..].matches('-').count() == 1
}

/// `safe_dump`'s spelling of a string: plain where the emitter allows it,
/// single-quoted for printable text, double-quoted with YAML's escapes
/// otherwise (the one place this door differs from PyYAML, which folds a
/// line break into single quotes).
pub fn spell_str(text: &str) -> String {
    if text.is_empty() {
        return "''".to_string();
    }
    if plain_allowed(text) {
        return text.to_string();
    }
    if text.chars().all(printable) {
        return format!("'{}'", text.replace('\'', "''"));
    }
    let mut out = String::from("\"");
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\0' => out.push_str("\\0"),
            '\u{7}' => out.push_str("\\a"),
            '\u{8}' => out.push_str("\\b"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\u{b}' => out.push_str("\\v"),
            '\u{c}' => out.push_str("\\f"),
            '\r' => out.push_str("\\r"),
            '\u{1b}' => out.push_str("\\e"),
            '\u{85}' => out.push_str("\\N"),
            '\u{2028}' => out.push_str("\\L"),
            '\u{2029}' => out.push_str("\\P"),
            ch if printable(ch) => out.push(ch),
            ch if (ch as u32) < 0x100 => out.push_str(&format!("\\x{:02X}", ch as u32)),
            ch if (ch as u32) < 0x10000 => out.push_str(&format!("\\u{:04X}", ch as u32)),
            ch => out.push_str(&format!("\\U{:08X}", ch as u32)),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn a_plain_word_types_as_yaml_1_1_does() {
        for (word, value) in [
            ("kept", json!("kept")),
            ("3", json!(3)),
            ("-7", json!(-7)),
            ("1_000", json!(1000)),
            ("0x1f", json!(31)),
            ("010", json!(8)),
            ("0", json!(0)),
            ("True", json!(true)),
            ("off", json!(false)),
            ("~", json!(null)),
            ("", json!(null)),
            ("1.5", json!(1.5)),
            ("1.", json!(1.0)),
            (".5", json!(0.5)),
            ("1e5", json!("1e5")),
            ("2026-01-01", json!("2026-01-01")),
            ("a b # c", json!("a b")),
            ("'it''s'", json!("it's")),
            (r#""a\tb\u00e9""#, json!("a\tbé")),
            ("'quoted' # note", json!("quoted")),
        ] {
            assert_eq!(read(word), Some(value), "{word}");
        }
        for word in [
            "[a, b]", "{a: 1}", "- a", "a: b", "'open", "\"\\q\"", "|", "&anchor",
        ] {
            assert_eq!(read(word), None, "{word}");
        }
    }

    #[test]
    fn a_scalar_is_spelled_as_safe_dump_spells_it() {
        for (value, text) in [
            (json!("kept"), "kept"),
            (json!("a: b"), "'a: b'"),
            (json!("true"), "'true'"),
            (json!("3"), "'3'"),
            (json!("1.5"), "'1.5'"),
            (json!("2026-01-01"), "'2026-01-01'"),
            (json!(""), "''"),
            (json!("- x"), "'- x'"),
            (json!("-x"), "-x"),
            (json!("x #y"), "'x #y'"),
            (json!("x#y"), "x#y"),
            (json!("it's"), "it's"),
            (json!("'quoted'"), "'''quoted'''"),
            (json!("a\tb"), "\"a\\tb\""),
            (json!("a\nb"), "\"a\\nb\""),
            (json!("café ✓"), "café ✓"),
            (json!(" lead"), "' lead'"),
            (json!("client:default/me"), "client:default/me"),
            (json!(3), "3"),
            (json!(true), "true"),
            (json!(null), "null"),
            (json!(1.5), "1.5"),
            (json!(1e-7), "1.0e-07"),
        ] {
            assert_eq!(spell(&value).as_deref(), Some(text), "{value}");
        }
        assert_eq!(
            read(&spell(&json!([1, {"x": "y"}])).unwrap()),
            Some(json!([1, {"x": "y"}]))
        );
        assert_eq!(read(&spell_str("a\tb")), Some(json!("a\tb")));
    }
}
