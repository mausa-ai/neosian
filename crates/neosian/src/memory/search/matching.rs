//! Matching uses per-scalar Unicode lowercase, never a database collation or tokenizer.

use serde_json::Value;

use super::{Excerpt, Request};
use crate::store::{Document, error::Error};

// Version 1's matching tables are part of the optional wire contract.
const _: () = assert!(matches!(char::UNICODE_VERSION, (17, 0, 0)));

fn lower(text: &str) -> String {
    text.chars().flat_map(char::to_lowercase).collect()
}

pub(super) fn terms(query: &str) -> Vec<String> {
    let mut terms: Vec<_> = query.split_whitespace().map(lower).collect();
    terms.sort();
    terms.dedup();
    terms
}

fn strings<'a>(value: &'a Value, out: &mut Vec<&'a str>) {
    match value {
        Value::String(text) => out.push(text),
        Value::Array(items) => items.iter().for_each(|v| strings(v, out)),
        Value::Object(items) => {
            let mut keys: Vec<_> = items.keys().collect();
            keys.sort();
            for key in keys {
                strings(&items[key], out);
            }
        }
        _ => {}
    }
}

fn excerpt(text: &str, at: usize, field: &str, key: Option<&str>) -> Excerpt {
    let mut bytes = 0;
    let position = text
        .chars()
        .position(|ch| {
            bytes += ch.to_lowercase().map(char::len_utf8).sum::<usize>();
            bytes > at
        })
        .unwrap_or(0);
    let offset = position.saturating_sub(40);
    Excerpt {
        field: field.into(),
        metadata_key: key.map(str::to_string),
        offset,
        text: text.chars().skip(offset).take(240).collect(),
    }
}

pub(super) fn find(
    scope: &str,
    path: &str,
    document: &Document,
    request: &Request,
    terms: &[String],
) -> Result<Option<(Excerpt, Vec<String>)>, Error> {
    let metadata = if request.fields.iter().any(|f| f == "metadata") {
        crate::wire::encode::extra_json(&document.envelope, scope, path)?
    } else {
        Value::Null
    };
    let mut values = Vec::new();
    // Stable excerpt preference: body, then requested keys, then path.
    if request.fields.iter().any(|f| f == "content") {
        values.push(("content", None, document.content.as_str()));
    }
    for key in &request.metadata_keys {
        let mut selected = Vec::new();
        strings(&metadata[key], &mut selected);
        values.extend(
            selected
                .into_iter()
                .map(|s| ("metadata", Some(key.as_str()), s)),
        );
    }
    if request.fields.iter().any(|f| f == "path") {
        values.push(("path", None, path));
    }
    let mut matched = vec![false; terms.len()];
    let mut chosen = None;
    let mut fields = Vec::new();
    for (field, key, text) in values {
        let folded = lower(text);
        let mut first = None;
        for (index, term) in terms.iter().enumerate() {
            if let Some(at) = folded.find(term) {
                matched[index] = true;
                first = Some(first.map_or(at, |before: usize| before.min(at)));
            }
        }
        if let Some(at) = first {
            if chosen.is_none() {
                chosen = Some(excerpt(text, at, field, key));
            }
            if !fields.iter().any(|f| f == field) {
                fields.push(field.into());
            }
        }
    }
    Ok(if matched.iter().all(|m| *m) {
        chosen.map(|e| (e, fields))
    } else {
        None
    })
}
