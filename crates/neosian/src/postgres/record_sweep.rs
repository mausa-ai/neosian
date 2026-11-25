//! Sweep stale open owners in conversation order (§40.54 item 13).
use serde_json::{Value, json};

use super::{PostgresStore, sql, values};
use crate::conversation::turn_records::sweep::{
    SweepConversation, SweepPage, cursor_after, start_after,
};
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::remote::codec;

fn entry(id: &str, row: &Value) -> Result<SweepConversation, Error> {
    // PostgreSQL sums bigint as numeric. Preserve the full u64 accounting
    // range and refuse a negative owner even when other owners offset it.
    let freed_bytes = row["freed"]
        .as_str()
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| {
            Refused::format_unsupported(id, "stale item byte counts are invalid or overflow")
        })?;
    Ok(SweepConversation {
        conversation_id: id.to_owned(),
        drafts: usize::try_from(codec::number(row, "drafts")?)
            .map_err(|_| Error::Value("stale draft count overflows".into()))?,
        restores: usize::try_from(codec::number(row, "restores")?)
            .map_err(|_| Error::Value("stale restore count overflows".into()))?,
        freed_bytes,
    })
}

impl PostgresStore {
    pub fn sweep_records(
        &self,
        limit: usize,
        cursor: Option<&str>,
        write: bool,
    ) -> Result<SweepPage, Error> {
        let mut after = start_after(limit, cursor)?.unwrap_or_default();
        self.require_records()?;
        let seconds = limits::Records::load()?.draft_seconds;
        let clock = self.one(
            sql!("records/sweep_cutoff"),
            json!({"seconds":seconds}),
            false,
        )?;
        let cutoff = codec::text(&clock, "cutoff")?;
        let candidates = |after: &str, count: usize| {
            self.fetch(
                sql!("records/sweep_page"),
                json!({"cutoff":cutoff,"after":after,"limit":count}),
            )
        };
        let mut conversations = Vec::with_capacity(limit);
        let next_cursor = if !write {
            // One read-only statement supplies the page and its lookahead.
            let rows = candidates(&after, limit + 1)?;
            for row in rows.iter().take(limit) {
                conversations.push(entry(&codec::text(row, "conversation_id")?, row)?);
            }
            (rows.len() > limit).then(|| cursor_after(&conversations[limit - 1].conversation_id))
        } else {
            let discard = sql!("records/sweep_discard").replace("{{schema}}", self.pool.schema());
            loop {
                let remaining = limit - conversations.len();
                let rows = candidates(&after, remaining.max(1))?;
                if remaining == 0 {
                    break (!rows.is_empty()).then(|| cursor_after(&after));
                }
                let exhausted = rows.len() < remaining;
                for row in rows {
                    let id = codec::text(&row, "conversation_id")?;
                    let removed = self.pool.record_mutation(&id, async |tx| {
                        let row = tx.query_one(&discard, &[&id, &cutoff]).await?;
                        let summary = values::row(&row).map_err(Error::from)?;
                        Ok(entry(&id, &summary)?)
                    })?;
                    // A refresh, seal or another sweep can win the lock first.
                    // Advance past that candidate and refill from later ids.
                    after = id;
                    if removed.drafts != 0 || removed.restores != 0 {
                        conversations.push(removed);
                    }
                }
                if exhausted {
                    break None;
                }
            }
        };
        Ok(SweepPage {
            conversations,
            next_cursor,
            write,
        })
    }
}
