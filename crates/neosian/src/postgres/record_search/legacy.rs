//! Search the records produced from one stored format-1 turn at a time.
//! PostgreSQL decides membership for converted and physical records under
//! the same collation. The stored legacy search text is a turn-wide field.
use super::{Candidates, Context, native};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{Kind, TurnRecord, record_facts, records_of};
use crate::conversation::{Error, Refused};
use crate::postgres::{connection::failure, record_read, rows, sql};
use crate::store::time::Stamp;
use tokio_postgres::{Statement, types::ToSql};

pub(super) async fn records(
    cx: &Context<'_, '_>,
    id: &str,
    turn: u64,
) -> Result<(Vec<TurnRecord>, Option<String>), Error> {
    let whole = record_read::whole_at(cx.tx, cx.statements, id, turn, cx.budget).await?;
    let converted = records_of(&whole.messages)
        .map_err(|why| Refused::format_unsupported(id, &format!("turn {turn}: {why}")))?;
    Ok((converted, whole.actor))
}

struct Matcher {
    statement: Statement,
    patterns: Vec<String>,
}

impl Matcher {
    async fn new(cx: &Context<'_, '_>) -> Result<Self, Error> {
        let patterns: Vec<_> = cx
            .search
            .terms
            .iter()
            .map(|term| format!("%{}%", rows::escaped(term)))
            .collect();
        let predicate = native::predicate(patterns.len(), cx.search.order, 3);
        let sql = format!(
            "SELECT r.n FROM unnest($1::text[], $2::text[]) WITH ORDINALITY
            AS r(text, name, n) WHERE {predicate} ORDER BY r.n"
        );
        let statement = cx.tx.prepare(&sql).await.map_err(failure)?;
        Ok(Self {
            statement,
            patterns,
        })
    }

    /// Zero-based positions of selected records. Only the text and an
    /// actual tool call's name are sent in the two parallel arrays.
    async fn positions(
        &self,
        cx: &Context<'_, '_>,
        batch: &[&TurnRecord],
    ) -> Result<Vec<usize>, Error> {
        let texts: Vec<_> = batch.iter().map(|r| r.text.as_str()).collect();
        let names: Vec<_> = batch
            .iter()
            .map(|r| r.name.as_deref().filter(|_| r.kind == Kind::ToolCall))
            .collect();
        let mut params: Vec<&(dyn ToSql + Sync)> = vec![&texts, &names];
        params.extend(self.patterns.iter().map(|p| p as &(dyn ToSql + Sync)));
        let found = cx
            .tx
            .query(&self.statement, &params)
            .await
            .map_err(failure)?;
        Ok(found
            .iter()
            .map(|row| (row.get::<_, i64>(0) - 1) as usize)
            .collect())
    }
}

pub(super) async fn offer(cx: &Context<'_, '_>, rank: &mut Candidates) -> Result<(), Error> {
    let headers = sql!("records/search_legacy_headers").replace("{{schema}}", cx.schema);
    let headers = cx.tx.prepare(&headers).await.map_err(failure)?;
    let (since, before) = cx.search.bounds;
    let date = |micros| Stamp::from_micros(micros).z();
    let portal = cx
        .tx
        .bind(
            &headers,
            &[
                &cx.search.conversations,
                &since.map(date),
                &before.map(date),
            ],
        )
        .await
        .map_err(failure)?;
    let matcher = Matcher::new(cx).await?;
    let newest = cx.search.order == Order::Newest;
    let mut selected = 0;
    loop {
        // Header rows hold only keys. Keep database round trips bounded
        // without buffering the messages of multiple legacy turns.
        let keys = cx.tx.query_portal(&portal, 64).await.map_err(failure)?;
        if keys.is_empty() {
            return Ok(());
        }
        for row in keys {
            let id: &str = row.try_get("conversation_id").map_err(failure)?;
            let turn = row.try_get::<_, i32>("turn").map_err(failure)? as u64;
            let created = row.try_get("created").map_err(failure)?;
            let (converted, _) = records(cx, id, turn).await?;
            for batch in converted.chunks(4) {
                let searchable: Vec<_> = batch
                    .iter()
                    .filter(|r| cx.search.kinds.contains(&r.kind))
                    .collect();
                if searchable.is_empty() {
                    continue;
                }
                for index in matcher.positions(cx, &searchable).await? {
                    let record = searchable[index];
                    let facts = (!newest).then(|| record_facts(record, &cx.search.terms));
                    rank.offer(id, created, turn, record.seq, true, facts)?;
                    selected += 1;
                    if newest && selected == cx.search.limit {
                        return Ok(());
                    }
                }
            }
        }
    }
}
