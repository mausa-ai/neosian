//! History membership is one key per turn. Each term may match a different
//! record of its visible mapping. Whole turns retain their stored search text.
#[cfg(test)]
#[path = "candidate_tests.rs"]
mod tests;

use super::Context;
use crate::conversation::Error;
use crate::conversation::page::Resume;
use crate::conversation::rank::{Order, Request};
use crate::postgres::{connection::failure, record_search::predicate, rows::escaped, sql};
use crate::store::time::Stamp;
use tokio_postgres::{Portal, types::ToSql};

const FIRST_PATTERN: usize = 9;
const EXPANDED: usize = 16;
const ARRAY_TERM: &str = "(r.text ILIKE wanted.pattern OR r.name ILIKE wanted.pattern)";

fn native_match(order: Order, terms: usize, scoped: bool) -> String {
    let exists = |p: &str| {
        sql!("records/history_exists")
            .trim()
            .replace("{{match}}", p)
    };
    if terms == 0 {
        return (order == Order::Newest).to_string();
    }
    if order == Order::Relevance && !scoped {
        let (patterns, condition, barrier) = if terms > EXPANDED {
            // Keep each pattern on the outside of its own parameterized
            // scan, so the planner can probe both trigram indexes for it.
            (
                "unnest($1::text[]) AS wanted(pattern) CROSS JOIN LATERAL",
                ARRAY_TERM.to_owned(),
                "OFFSET 0",
            )
        } else {
            ("", predicate(terms, order, FIRST_PATTERN), "")
        };
        return sql!("records/history_matching_owners")
            .replace("{{patterns}}", patterns)
            .replace("{{match}}", &condition)
            .replace("{{barrier}}", barrier);
    }
    if terms > EXPANDED {
        // The original history grammar accepts any number of terms. One
        // array keeps this statement and its parameter count bounded.
        let matched = exists(ARRAY_TERM);
        return match order {
            Order::Newest => {
                // One mandatory, independently indexable term narrows the
                // turn mappings before checking the rest of a long query.
                let first = exists("r.text ILIKE ($1::text[])[1] OR r.name ILIKE ($1::text[])[1]");
                format!(
                    "{first} AND NOT EXISTS (SELECT 1 FROM unnest($1::text[]) AS wanted(pattern) WHERE NOT {matched})"
                )
            }
            Order::Relevance => format!(
                "EXISTS (SELECT 1 FROM unnest($1::text[]) AS wanted(pattern) WHERE {matched})"
            ),
        };
    }
    match order {
        Order::Newest => (FIRST_PATTERN..FIRST_PATTERN + terms)
            .map(|at| exists(&predicate(1, order, at)))
            .collect::<Vec<_>>()
            .join(" AND "),
        Order::Relevance => exists(&predicate(terms, order, FIRST_PATTERN)),
    }
}

fn statement(schema: &str, order: Order, terms: usize, scoped: bool) -> String {
    let (legacy_match, legacy_text, ordering) = match order {
        Order::Newest => (
            "ALL",
            "NULL::text",
            "ORDER BY created_at DESC, conversation_id COLLATE \"C\" DESC, turn DESC",
        ),
        Order::Relevance => ("ANY", "t.search_text", ""),
    };
    sql!("records/history_candidates")
        .replace("{{legacy_match}}", legacy_match)
        .replace("{{legacy_text}}", legacy_text)
        .replace("{{native_match}}", &native_match(order, terms, scoped))
        .replace("{{ordering}}", ordering)
        .replace("{{schema}}", schema)
}

pub(super) async fn open(
    cx: &Context<'_, '_>,
    request: &Request,
    resume: &Resume,
) -> Result<Portal, Error> {
    let mut terms: Vec<_> = request.terms.iter().collect();
    terms.sort_unstable();
    terms.dedup();
    let patterns: Vec<_> = terms
        .iter()
        .map(|term| format!("%{}%", escaped(term)))
        .collect();
    let (at, id, number) = match (request.order, resume) {
        (Order::Newest, Resume::After(last)) => (
            Some(Stamp::from_micros(last.created).z()),
            Some(last.conversation.as_str()),
            Some(i64::try_from(last.turn).unwrap_or(i64::MAX)),
        ),
        _ => (None, None, None),
    };
    let limit =
        (request.order == Order::Newest).then(|| i64::try_from(request.limit).unwrap_or(i64::MAX));
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![
        &patterns,
        &request.ids,
        &request.since,
        &request.before,
        &at,
        &id,
        &number,
        &limit,
    ];
    if patterns.len() <= EXPANDED {
        params.extend(patterns.iter().map(|p| p as &(dyn ToSql + Sync)));
    }
    let scoped = request.ids.is_some() || request.since.is_some() || request.before.is_some();
    let query = statement(cx.schema, request.order, patterns.len(), scoped);
    // A varying predicate must not be cached under one static template key.
    cx.tx
        .bind(query.as_str(), &params)
        .await
        .map_err(failure)
        .map_err(Into::into)
}
