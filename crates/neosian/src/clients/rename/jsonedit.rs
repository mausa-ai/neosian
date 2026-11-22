//! Edits on a JSON document's text (DESIGN §40.53): every byte outside
//! the edit stays, so indentation, escapes and member order are kept. Each
//! edit is checked by parsing the result against the intended document.

use serde_json::Value;

/// An RFC 6901 pointer's segments.
pub fn segments(pointer: &str) -> Vec<String> {
    pointer
        .split('/')
        .skip(1)
        .map(|s| s.replace("~1", "/").replace("~0", "~"))
        .collect()
}

/// One segment escaped for a pointer.
pub fn escape(segment: &str) -> String {
    segment.replace('~', "~0").replace('/', "~1")
}

pub fn join(segments: &[&str]) -> String {
    segments.iter().map(|s| format!("/{}", escape(s))).collect()
}

fn ws(text: &[u8], mut at: usize) -> usize {
    while at < text.len() && matches!(text[at], b' ' | b'\t' | b'\n' | b'\r') {
        at += 1;
    }
    at
}

fn string_end(text: &[u8], mut at: usize) -> Option<usize> {
    if text.get(at) != Some(&b'"') {
        return None;
    }
    at += 1;
    while at < text.len() {
        match text[at] {
            b'\\' => at += 2,
            b'"' => return Some(at + 1),
            _ => at += 1,
        }
    }
    None
}

/// A byte range in the text.
type Span = (usize, usize);

/// Members (key span, value span) of an object, or elements of an array.
struct Container {
    open: usize,
    items: Vec<(Option<Span>, Span)>,
}

fn container(text: &[u8], at: usize) -> Option<Container> {
    let object = match text.get(at)? {
        b'{' => true,
        b'[' => false,
        _ => return None,
    };
    let close = if object { b'}' } else { b']' };
    let mut items = vec![];
    let mut i = ws(text, at + 1);
    if text.get(i) == Some(&close) {
        return Some(Container { open: at, items });
    }
    loop {
        let key = if object {
            let end = string_end(text, i)?;
            let key = (i, end);
            i = ws(text, end);
            if text.get(i) != Some(&b':') {
                return None;
            }
            i = ws(text, i + 1);
            Some(key)
        } else {
            None
        };
        let end = value_end(text, i)?;
        items.push((key, (i, end)));
        i = ws(text, end);
        match text.get(i)? {
            b',' => i = ws(text, i + 1),
            c if *c == close => return Some(Container { open: at, items }),
            _ => return None,
        }
    }
}

fn value_end(text: &[u8], at: usize) -> Option<usize> {
    match text.get(at)? {
        b'"' => string_end(text, at),
        b'{' | b'[' => {
            let found = container(text, at)?;
            let last = found.items.last().map_or(found.open + 1, |item| item.1.1);
            let close = ws(text, last);
            Some(close + 1)
        }
        _ => {
            let mut end = at;
            while end < text.len() && !b",]} \t\r\n".contains(&text[end]) {
                end += 1;
            }
            (end > at).then_some(end)
        }
    }
}

fn key_of(text: &str, span: (usize, usize)) -> Option<String> {
    serde_json::from_str(&text[span.0..span.1]).ok()
}

/// The span of the value at `segments`.
pub fn locate(text: &str, segments: &[String]) -> Option<(usize, usize)> {
    let bytes = text.as_bytes();
    let start = ws(bytes, 0);
    let mut span = (start, value_end(bytes, start)?);
    for segment in segments {
        let found = container(bytes, span.0)?;
        span = found
            .items
            .iter()
            .enumerate()
            .find(|(index, (key, _))| match key {
                Some(key) => key_of(text, *key).as_deref() == Some(segment.as_str()),
                None => segment.parse::<usize>().ok() == Some(*index),
            })?
            .1
            .1;
    }
    Some(span)
}

fn checked(edited: String, intended: &Value) -> Option<String> {
    let parsed: Value = serde_json::from_str(&edited).ok()?;
    (parsed == *intended).then_some(edited)
}

/// The member `key` of the object at `segments` removed with its separator.
pub fn remove_member(
    text: &str,
    segments: &[String],
    key: &str,
    intended: &Value,
) -> Option<String> {
    let (from, to) = member_span(text, segments, key)?;
    checked(format!("{}{}", &text[..from], &text[to..]), intended)
}

/// The bytes removing member `key` of the object at `segments` takes: the
/// member and one separator (the comma before it, or after it when first).
pub fn member_span(text: &str, segments: &[String], key: &str) -> Option<(usize, usize)> {
    let bytes = text.as_bytes();
    let found = container(bytes, locate(text, segments)?.0)?;
    let index = found
        .items
        .iter()
        .position(|(k, _)| k.and_then(|k| key_of(text, k)).as_deref() == Some(key))?;
    let (member, value) = (found.items[index].0?, found.items[index].1);
    Some(if index > 0 {
        (found.items[index - 1].1.1, value.1)
    } else if let Some(next) = found.items.get(1) {
        (member.0, next.0?.0)
    } else {
        (member.0, value.1)
    })
}

/// A member `key` added as the first member of the object at `segments`,
/// with the spacing the object already uses.
pub fn insert_member(
    text: &str,
    segments: &[String],
    key: &str,
    value_text: &str,
    intended: &Value,
) -> Option<String> {
    let bytes = text.as_bytes();
    let found = container(bytes, locate(text, segments)?.0)?;
    let open = found.open + 1;
    let member = format!("{}: {value_text}", serde_json::to_string(key).ok()?);
    let edited = match found.items.first() {
        Some((Some(first), _)) => {
            let spacing = &text[open..first.0];
            format!("{}{spacing}{member},{}", &text[..open], &text[open..])
        }
        _ => format!("{}{member}{}", &text[..open], &text[open..]),
    };
    checked(edited, intended)
}

/// The member key `old` of the object at `segments` renamed to `new`.
pub fn rename_key(
    text: &str,
    segments: &[String],
    old: &str,
    new: &str,
    intended: &Value,
) -> Option<String> {
    let bytes = text.as_bytes();
    let found = container(bytes, locate(text, segments)?.0)?;
    let (start, end) = found
        .items
        .iter()
        .find_map(|(k, _)| k.filter(|k| key_of(text, *k).as_deref() == Some(old)))?;
    let quoted = serde_json::to_string(new).ok()?;
    checked(
        format!("{}{quoted}{}", &text[..start], &text[end..]),
        intended,
    )
}
