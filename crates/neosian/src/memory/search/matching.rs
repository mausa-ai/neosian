//! Matching uses per-scalar Unicode lowercase, never a database collation or tokenizer.

use serde_json::Value;

use super::{Excerpt, Passage, Request};
use crate::store::{Document, error::Error};

// Version 1's matching tables are part of the optional wire contract.
const _: () = assert!(matches!(char::UNICODE_VERSION, (17, 0, 0)));

/// Per-scalar Unicode lowercase, version 1's fold (record search shares it).
pub(crate) fn lower(text: &str) -> String {
    text.chars().flat_map(char::to_lowercase).collect()
}

/// The distinct lowercased whitespace terms of a query, in ascending order.
pub(crate) fn terms(query: &str) -> Vec<String> {
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

/// Version 1's excerpt of `text` around the folded byte `at` where the
/// first match begins: up to 240 scalars starting up to 40 before the
/// match's scalar, with that start as a scalar offset (record search shares
/// it, DESIGN §40.54 item 11).
pub(crate) fn window(text: &str, at: usize) -> (usize, String) {
    let mut bytes = 0;
    let position = text
        .chars()
        .position(|ch| {
            bytes += ch.to_lowercase().map(char::len_utf8).sum::<usize>();
            bytes > at
        })
        .unwrap_or(0);
    let offset = position.saturating_sub(40);
    (offset, text.chars().skip(offset).take(240).collect())
}

fn excerpt(text: &str, at: usize, field: &str, key: Option<&str>) -> Excerpt {
    let (offset, text) = window(text, at);
    Excerpt {
        field: field.into(),
        metadata_key: key.map(str::to_string),
        offset,
        text,
    }
}

/// What one document holds for a query: the excerpt and matched fields when
/// any term occurs, whether every term occurs, and the passages when wanted.
pub(super) struct Measure {
    pub found: Option<(Excerpt, Vec<String>)>,
    pub all: bool,
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
    let measured = measure(scope, path, document, request, terms, false)?;
    Ok(if measured.all { measured.found } else { None })
}

/// The selected string values of one document in version 1 order: body, then
/// requested metadata keys, then path. `metadata` holds the decoded extras.
fn selected<'a>(
    path: &'a str,
    document: &'a Document,
    request: &'a Request,
    metadata: &'a Value,
) -> Vec<(&'static str, Option<&'a str>, &'a str)> {
    let mut values = Vec::new();
    if request.fields.iter().any(|f| f == "content") {
        values.push(("content", None, document.content.as_str()));
    }
    for key in &request.metadata_keys {
        let mut found = Vec::new();
        strings(&metadata[key], &mut found);
        values.extend(
            found
                .into_iter()
                .map(|s| ("metadata", Some(key.as_str()), s)),
        );
    }
    if request.fields.iter().any(|f| f == "path") {
        values.push(("path", None, path));
    }
    values
}

fn metadata(
    scope: &str,
    path: &str,
    document: &Document,
    request: &Request,
) -> Result<Value, Error> {
    if request.fields.iter().any(|f| f == "metadata") {
        Ok(crate::wire::encode::extra_json(
            &document.envelope,
            scope,
            path,
        )?)
    } else {
        Ok(Value::Null)
    }
}

/// What ranking keeps of one document (DESIGN §40.45): whether every term
/// occurs and, when counting, per-term non-overlapping occurrence counts and
/// the scalar length of the selected values. No excerpt, no passages.
pub(super) struct Tally {
    pub all: bool,
    pub counts: Vec<u32>,
    pub length: u64,
}

pub(super) fn tally(
    scope: &str,
    path: &str,
    document: &Document,
    request: &Request,
    terms: &[String],
    count: bool,
) -> Result<Tally, Error> {
    let extras = metadata(scope, path, document, request)?;
    let mut matched = vec![false; terms.len()];
    let mut counts = vec![0_u32; if count { terms.len() } else { 0 }];
    let mut length = 0_u64;
    for (_, _, text) in selected(path, document, request, &extras) {
        let folded = lower(text);
        if count {
            length += folded.chars().count() as u64;
        }
        for (index, term) in terms.iter().enumerate() {
            if count {
                let found = folded.matches(term.as_str()).count();
                if found > 0 {
                    matched[index] = true;
                    let found = u32::try_from(found).unwrap_or(u32::MAX);
                    counts[index] = counts[index].saturating_add(found);
                }
            } else if !matched[index] && folded.contains(term.as_str()) {
                matched[index] = true;
            }
        }
    }
    Ok(Tally {
        all: matched.iter().all(|m| *m),
        counts,
        length,
    })
}

/// The hit material for one document: excerpt, matched fields, whether every
/// term occurs and the passages, for path order's hits and ranked winners.
pub(super) fn measure(
    scope: &str,
    path: &str,
    document: &Document,
    request: &Request,
    terms: &[String],
    any: bool,
) -> Result<Measure, Error> {
    let extras = metadata(scope, path, document, request)?;
    let values = selected(path, document, request, &extras);
    let mut matched = vec![false; terms.len()];
    let mut chosen = None;
    let mut fields = Vec::new();
    for &(field, key, text) in &values {
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
    let all = matched.iter().all(|m| *m);
    // Passages only for documents that become hits under the requested order.
    let wanted = request.passage_count();
    let passages = (wanted > 0 && chosen.is_some() && (any || all))
        .then(|| super::passages::select(&values, terms, wanted));
    Ok(Measure {
        found: chosen.map(|e| (e, fields)),
        all,
        passages,
    })
}
