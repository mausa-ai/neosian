//! Python's `json.dumps` and `str()` over a JSON value (DESIGN §39.3): a
//! tool call's arguments and a result's `data` are spelled into log lines
//! by both doors, so the spelling is Python's, floats included.

use std::fmt::Write;

use serde_json::{Number, Value};

use crate::repr::repr;

/// How `json.dumps` was called: the escaping, the separators, the key order.
#[derive(Clone, Copy)]
struct Style {
    ascii: bool,
    spaced: bool,
    sorted: bool,
}

/// `json.dumps(value, separators=(",", ":"), ensure_ascii=ascii)`.
pub fn dumps(value: &Value, ascii: bool) -> String {
    let mut out = String::new();
    let style = Style {
        ascii,
        spaced: false,
        sorted: false,
    };
    write_value(&mut out, value, style);
    out
}

/// `json.dumps(value, ensure_ascii=ascii, sort_keys=sort_keys)`: the
/// default separators, a space after every comma and colon.
pub fn dumps_spaced(value: &Value, ascii: bool, sort_keys: bool) -> String {
    let mut out = String::new();
    let style = Style {
        ascii,
        spaced: true,
        sorted: sort_keys,
    };
    write_value(&mut out, value, style);
    out
}

/// `str(value)` of what `json.loads` returned: a string is itself, the
/// rest is its `repr`.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => py_repr(other),
    }
}

fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number_text(number),
        Value::String(text) => repr(text),
        Value::Array(items) => {
            let items: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(map) => {
            let pairs: Vec<String> = map
                .iter()
                .map(|(key, item)| format!("{}: {}", repr(key), py_repr(item)))
                .collect();
            format!("{{{}}}", pairs.join(", "))
        }
    }
}

fn write_value(out: &mut String, value: &Value, style: Style) {
    let comma = if style.spaced { ", " } else { "," };
    let colon = if style.spaced { ": " } else { ":" };
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => out.push_str(&number_text(number)),
        Value::String(text) => write_string(out, text, style.ascii),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(comma);
                }
                write_value(out, item, style);
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut pairs: Vec<(&String, &Value)> = map.iter().collect();
            if style.sorted {
                pairs.sort_by(|a, b| a.0.cmp(b.0));
            }
            out.push('{');
            for (index, (key, item)) in pairs.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(comma);
                }
                write_string(out, key, style.ascii);
                out.push_str(colon);
                write_value(out, item, style);
            }
            out.push('}');
        }
    }
}

fn write_string(out: &mut String, text: &str, ascii: bool) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ch if ch < ' ' => {
                let _ = write!(out, "\\u{:04x}", ch as u32);
            }
            ch if ascii && !ch.is_ascii() => {
                for unit in ch.encode_utf16(&mut [0; 2]) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
            ch => out.push(ch),
        }
    }
    out.push('"');
}

/// An integer as its digits, a float as `float.__repr__`.
fn number_text(number: &Number) -> String {
    match number.as_f64() {
        Some(float) if !number.is_i64() && !number.is_u64() => float_repr(float),
        _ => number.to_string(),
    }
}

/// `repr(float)`: the shortest round-trip digits, fixed notation while
/// the decimal point sits within (-4, 16], exponential past it with a
/// signed two-digit exponent, and a `.0` on a whole number.
pub fn float_repr(value: f64) -> String {
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific
        .split_once('e')
        .expect("a float formats with an exponent");
    let exponent: i32 = exponent.parse().expect("the exponent is an integer");
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(unsigned) => ("-", unsigned),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(|ch| *ch != '.').collect();
    let point = exponent + 1;
    let body = if point <= -4 || point > 16 {
        let (first, rest) = digits.split_at(1);
        let dot = if rest.is_empty() { "" } else { "." };
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{first}{dot}{rest}e{sign}{:02}", exponent.abs())
    } else if point <= 0 {
        format!("0.{}{digits}", "0".repeat(point.unsigned_abs() as usize))
    } else if point as usize >= digits.len() {
        format!("{digits}{}.0", "0".repeat(point as usize - digits.len()))
    } else {
        let (whole, fraction) = digits.split_at(point as usize);
        format!("{whole}.{fraction}")
    };
    format!("{sign}{body}")
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn dumps_is_pythons_compact_form() {
        let value = json!({"b": [1, 2.5, null, true], "a": "é \"q\" \n\u{1}\u{1f600}"});
        let escaped = concat!(
            r#"{"b":[1,2.5,null,true],"a":""#,
            "\\",
            "u00e9 ",
            r#"\"q\" \n"#,
            "\\",
            "u0001",
            "\\",
            "ud83d",
            "\\",
            r#"ude00"}"#
        );
        assert_eq!(dumps(&value, true), escaped);
        let raw = concat!(
            r#"{"b":[1,2.5,null,true],"a":"é \"q\" \n"#,
            "\\",
            r#"u0001😀"}"#
        );
        assert_eq!(dumps(&value, false), raw);
        let nested = json!({"b": {"z": 1, "y": [true, {"d": 0, "c": null}]}, "a": "é"});
        assert_eq!(
            dumps_spaced(&nested, false, true),
            r#"{"a": "é", "b": {"y": [true, {"c": null, "d": 0}], "z": 1}}"#
        );
        assert_eq!(
            dumps_spaced(&nested, true, false),
            r#"{"b": {"z": 1, "y": [true, {"d": 0, "c": null}]}, "a": "\u00e9"}"#
        );
    }

    #[test]
    fn str_is_pythons() {
        assert_eq!(py_str(&json!("plain")), "plain");
        assert_eq!(py_str(&json!(null)), "None");
        assert_eq!(
            py_str(&json!({"ok": true, "n": 2, "xs": ["a", 1.0]})),
            "{'ok': True, 'n': 2, 'xs': ['a', 1.0]}"
        );
    }

    #[test]
    fn floats_print_as_repr_prints_them() {
        for (value, text) in [
            (0.0, "0.0"),
            (1.5, "1.5"),
            (-2.0, "-2.0"),
            (100000.0, "100000.0"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (2.5e-7, "2.5e-07"),
            (1e16, "1e+16"),
            (1234567890123456.0, "1234567890123456.0"),
            (0.1 + 0.2, "0.30000000000000004"),
        ] {
            assert_eq!(float_repr(value), text);
        }
    }
}
