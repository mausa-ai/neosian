//! Matching uses per-scalar Unicode lowercase, never a database collation or tokenizer.

use serde_json::Value;

use super::{Excerpt, Passage, Request};
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

/// What one document holds for a query: the excerpt and matched fields when
/// any term occurs, whether every term occurs, and (when counting) per-term
/// non-overlapping occurrence counts and the scalar length of selected values.
pub(super) struct Measure {
    pub found: Option<(Excerpt, Vec<String>)>,
    pub all: bool,
    pub counts: Vec<u32>,
    pub length: u64,
    pub passages: Option<Vec<Passage>>,
}

#[cfg(test)]
pub(super) fn find(
    scope: &str,
    path: &str,
    document: &Document,
    request: &Request,
    terms: &[String],
) -> Result<Option<(Excerpt, Vec<String>)>, Error> {
    let measured = measure(scope, path, document, request, terms, false, false)?;
    Ok(if measured.all { measured.found } else { None })
}

pub(super) fn measure(
    scope: &str,
    path: &str,
    document: &Document,
    request: &Request,
    terms: &[String],
    count: bool,
    any: bool,
) -> Result<Measure, Error> {
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
    let mut counts = vec![0_u32; if count { terms.len() } else { 0 }];
    let mut length = 0_u64;
    let mut chosen = None;
    let mut fields = Vec::new();
    for &(field, key, text) in &values {
        let folded = lower(text);
        if count {
            length += folded.chars().count() as u64;
        }
        let mut first = None;
        for (index, term) in terms.iter().enumerate() {
            if let Some(at) = folded.find(term) {
                matched[index] = true;
                first = Some(first.map_or(at, |before: usize| before.min(at)));
                if count {
                    let found = u32::try_from(folded.matches(term.as_str()).count());
                    counts[index] = counts[index].saturating_add(found.unwrap_or(u32::MAX));
                }
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
    let all = matched.iter().all(|m| *m);
    // Passages only for documents that become hits under the requested order.
    let wanted = request.passage_count();
    let passages = (wanted > 0 && chosen.is_some() && (any || all))
        .then(|| super::passages::select(&values, terms, wanted));
    Ok(Measure {
        found: chosen.map(|e| (e, fields)),
        all,
        counts,
        length,
        passages,
    })
}
