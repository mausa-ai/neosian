//! History search across published format-1 and format-2 turns. SQL selects
//! turns under its collation. Relevance scores each selected turn once and
//! reads the winners in the same snapshot, including oversized turns' places.
mod candidates;
#[cfg(test)]
mod tests;

use super::{PostgresStore, connection::failure, record_read, turns::check_ids, values};
use crate::conversation::page::{Hit, HitVisit, Mode, Resume, Visit, owed};
use crate::conversation::rank::{Key, Order, Ranking, Request};
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::remote::codec;
use serde_json::Value;
use tokio_postgres::Transaction;

pub(super) struct Context<'a, 'c> {
    pub(super) tx: &'a Transaction<'c>,
    pub(super) schema: &'a str,
    pub(super) statements: &'a record_read::Statements,
    pub(super) budget: u64,
    pub(super) maximum: u64,
}

impl PostgresStore {
    /// Direct callers receive the answerable winners of the same search as the route.
    pub fn rank_each(
        &self,
        request: &Request,
        resume: &Resume,
        mode: Mode,
        visit: &mut Visit,
    ) -> Result<Option<String>, Error> {
        self.rank_hits(request, resume, mode, &mut |hit, score| match hit {
            Hit::Turn(turn) => visit(turn, score),
            Hit::Omitted(_) => Ok(true),
        })
    }

    /// History ranking version 1, including the position of each oversized
    /// records winner so the route can count it and resume after it.
    pub fn rank_hits(
        &self,
        request: &Request,
        resume: &Resume,
        mode: Mode,
        visit: &mut HitVisit,
    ) -> Result<Option<String>, Error> {
        check_ids(request.ids.as_deref())?;
        request.bounds().map_err(Error::Value)?;
        if self.supports_records()? {
            return self.mixed_history(request, resume, visit);
        }
        self.whole_history(request, resume, mode, &mut |turn, score| {
            visit(Hit::Turn(turn), score)
        })
    }

    fn mixed_history(
        &self,
        request: &Request,
        resume: &Resume,
        visit: &mut HitVisit,
    ) -> Result<Option<String>, Error> {
        let budget = limits::response_bytes()?;
        let maximum = limits::Records::load()?.max_turn_bytes;
        self.pool.record_snapshot(async |tx, statements| {
            let cx = Context {
                tx,
                schema: self.pool.schema(),
                statements,
                budget,
                maximum,
            };
            cx.search(request, resume, visit).await
        })
    }
}

impl Context<'_, '_> {
    async fn search(
        &self,
        request: &Request,
        resume: &Resume,
        visit: &mut HitVisit<'_>,
    ) -> Result<Option<String>, Error> {
        let mut ranking = match request.order {
            Order::Newest => None,
            Order::Relevance => Some(Ranking::new(request, true)?),
        };
        let portal = candidates::open(self, request, resume).await?;
        // Newest streams just keys in answer order. A relevance row can
        // contain a complete legacy search_text, so fetch only one at once.
        let batch = if ranking.is_some() { 1 } else { 64 };
        loop {
            let rows = self
                .tx
                .query_portal(&portal, batch)
                .await
                .map_err(failure)?;
            for row in &rows {
                let row = values::row(row)?;
                let id = codec::text(&row, "conversation_id")?;
                let turn = codec::number(&row, "turn")?;
                let format = row["neosian_format"].as_i64().unwrap_or(0);
                if !matches!(format, 1 | 2) {
                    return Err(Refused::format_unsupported(
                        &id,
                        &format!("turn row {turn} declares unsupported format {format}"),
                    )
                    .into());
                }
                match &mut ranking {
                    None => {
                        if !visit(self.hit(&id, turn).await?, None)? {
                            return Ok(None);
                        }
                    }
                    Some(ranking) => self.offer(ranking, &row, &id, turn, format).await?,
                }
            }
            if rows.len() < batch as usize {
                break;
            }
        }
        let Some(ranking) = ranking else {
            return Ok(None);
        };
        let digest = ranking.digest();
        for (key, id, score) in owed(ranking.winners(), resume, &digest)? {
            if !visit(self.hit(&id, key.turn).await?, score)? {
                break;
            }
        }
        Ok(Some(digest))
    }

    async fn offer(
        &self,
        ranking: &mut Ranking,
        row: &Value,
        id: &str,
        turn: u64,
        format: i64,
    ) -> Result<(), Error> {
        let key = Key {
            created: codec::moment(row, "created_at")?.instant(),
            conversation: ranking.conversation(id),
            turn,
            place: 0,
            line: 0,
        };
        if format == 1 {
            // As in the base schema, only a winner's outgoing row is
            // charged against the response budget. The SQL-selected text
            // preserves the recorded spelling of tool arguments.
            return ranking.offer(key, row["search_text"].as_str().unwrap_or_default());
        }
        let (counts, length) = record_read::history_facts(
            self.tx,
            self.statements,
            id,
            turn,
            ranking.terms(),
            self.budget,
            self.maximum,
        )
        .await?;
        ranking.offer_facts(key, &counts, || length)
    }

    async fn hit(&self, id: &str, turn: u64) -> Result<Hit, Error> {
        record_read::history_hit(
            self.tx,
            self.statements,
            id,
            turn,
            self.budget,
            self.maximum,
        )
        .await
    }
}
