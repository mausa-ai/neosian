//! SQL selects native records under the database's collation. Only the
//! relevance pass needs a body to count occurrences for its candidate.
#[cfg(test)]
#[path = "native_tests.rs"]
mod tests;

use super::{Candidates, Context};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::record_facts;
use crate::conversation::{Error, Refused};
use crate::postgres::{
    connection::failure,
    record_read::{decode_record, one_record},
    rows::escaped,
    sql, values,
};
use crate::store::time::Stamp;
use serde_json::Value;
use tokio_postgres::types::ToSql;

/// Expand the already validated, distinct terms into separate predicates so
/// operator-supplied trigram indexes on either unmodified column can be used.
pub(in crate::postgres) fn predicate(terms: usize, order: Order, first: usize) -> String {
    let operator = match order {
        Order::Newest => " AND ",
        Order::Relevance => " OR ",
    };
    (first..first + terms)
        .map(|i| format!("(r.text ILIKE ${i} OR r.name ILIKE ${i})"))
        .collect::<Vec<_>>()
        .join(operator)
}

fn statement(schema: &str, terms: usize, order: Order) -> String {
    let (facts, ordering) = match order {
        Order::Newest => (
            "",
            "ORDER BY t.created_at DESC, r.conversation_id COLLATE \"C\" DESC, t.turn DESC, r.seq ASC",
        ),
        Order::Relevance => (sql!("records/search_facts"), ""),
    };
    sql!("records/search_candidates")
        .replace("{{schema}}", schema)
        .replace("{{membership}}", &predicate(terms, order, 6))
        .replace("{{facts}}", facts)
        .replace("{{order}}", ordering)
}

pub(super) async fn offer(cx: &Context<'_, '_>, rank: &mut Candidates) -> Result<(), Error> {
    let search = cx.search;
    let sql = statement(cx.schema, search.terms.len(), search.order);
    let kinds: Vec<_> = search.kinds.iter().map(|kind| kind.as_str()).collect();
    let stamp = |bound: Option<i64>| bound.map(|t| Stamp::from_micros(t).z());
    let (since, before) = (stamp(search.bounds.0), stamp(search.bounds.1));
    let newest = search.order == Order::Newest;
    let limit = newest.then_some(search.limit as i64);
    let patterns: Vec<_> = search
        .terms
        .iter()
        .map(|term| format!("%{}%", escaped(term)))
        .collect();
    let mut params: Vec<&(dyn ToSql + Sync)> =
        vec![&search.conversations, &kinds, &since, &before, &limit];
    params.extend(
        patterns
            .iter()
            .map(|pattern| pattern as &(dyn ToSql + Sync)),
    );
    let portal = cx.tx.bind(sql.as_str(), &params).await.map_err(failure)?;
    let batch = if newest { 64 } else { 1 };
    loop {
        let rows = cx.tx.query_portal(&portal, batch).await.map_err(failure)?;
        for row in &rows {
            let id: &str = row.try_get("conversation_id").map_err(failure)?;
            let turn: i32 = row.try_get("turn").map_err(failure)?;
            let seq: i32 = row.try_get("seq").map_err(failure)?;
            if !row.try_get::<_, bool>("valid").map_err(failure)? {
                return Err(Refused::format_unsupported(
                    id,
                    &format!("turn {turn}: invalid record mapping"),
                )
                .into());
            }
            let facts = if newest {
                None
            } else {
                let body: Value = row.try_get("body").map_err(failure)?;
                values::fits(&body, cx.budget)?;
                let record = decode_record(row, seq as u64, &body).ok_or_else(|| {
                    Refused::format_unsupported(
                        id,
                        &format!("turn {turn}: record {seq} is unreadable"),
                    )
                })?;
                Some(record_facts(&record, &search.terms))
            };
            rank.offer(
                id,
                row.try_get("created").map_err(failure)?,
                turn as u64,
                seq as u64,
                false,
                facts,
            )?;
        }
        if rows.len() < batch as usize {
            return Ok(());
        }
    }
}

/// Look up display metadata at a winner's turn. The optional name is the
/// first matching tool call in that sealed turn, irrespective of its role.
pub(super) async fn details(
    cx: &Context<'_, '_>,
    id: &str,
    turn: u64,
    call_id: Option<&str>,
) -> Result<(Option<String>, Option<String>), Error> {
    let query = sql!("records/search_details").replace("{{schema}}", cx.schema);
    let row = cx
        .tx
        .query_opt(query.as_str(), &[&id, &(turn as i32), &call_id])
        .await
        .map_err(failure)?
        .ok_or_else(|| {
            Refused::format_unsupported(
                id,
                &format!("turn {turn}: its record mapping is missing or invalid"),
            )
        })?;
    values::fits(&values::row(&row)?, cx.budget)?;
    let name = match row.try_get::<_, Option<i32>>("call_seq").map_err(failure)? {
        Some(seq) => {
            let seq = u64::try_from(seq).map_err(|_| {
                Refused::format_unsupported(
                    id,
                    &format!("turn {turn}: invalid tool call sequence {seq}"),
                )
            })?;
            one_record(cx.tx, cx.statements, id, turn, seq, cx.budget)
                .await?
                .name
        }
        None => None,
    };
    Ok((row.try_get("actor").map_err(failure)?, name))
}
