//! The write half of records extension 1. Only a mapping with part zero
//! denotes a sealed draft. Restore mappings can use the same public name.
mod draft;
#[cfg(test)]
mod tests;

use futures_util::TryStreamExt;
use serde_json::{Value, json};
use tokio_postgres::Transaction;

use super::connection::Failure;
use super::{PostgresStore, sql, values};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::turn_records::{
    Header, Refusal, Sizes, State, TurnRecord, Write, Written, conflict, size_refusal,
};
use crate::conversation::{Error, Refused};
use crate::limits::Records;

type Result<T> = std::result::Result<T, Failure>;

fn answer(write: &Write, state: State, next_seq: u64) -> Written {
    Written {
        draft: write.draft.clone(),
        state,
        next_seq,
        turn: None,
        stored_bytes: 0,
        freed_bytes: 0,
    }
}

fn pinned_size(id: &str, stated: Option<u64>, bytes: u64) -> Refused {
    conflict(
        id,
        "draft_size",
        json!({"turn_bytes":stated, "bytes":bytes}),
    )
}

fn actor(id: &str, write: &Write, original: Option<&str>) -> Result<()> {
    if write
        .actor
        .as_deref()
        .is_some_and(|given| Some(given) != original)
    {
        return Err(conflict(id, "draft_actor", json!({"draft":write.draft})).into());
    }
    Ok(())
}

fn corrupt(id: &str) -> Failure {
    Refused::format_unsupported(id, "a draft's stored state is unreadable").into()
}

pub(super) fn physical_row(record: &TurnRecord, last: bool) -> Value {
    let mut row = record.to_json(None);
    row["last"] = json!(last);
    let hex: String = record
        .digest(None)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    row["digest"] = Value::String(hex);
    row
}

impl PostgresStore {
    pub fn write_records(
        &self,
        id: &str,
        write: Write,
        limits: &Records,
    ) -> std::result::Result<Written, Error> {
        write.check().map_err(Error::Value)?;
        parse_conversation_id(id)?;
        self.require_records()?;
        self.pool.record_mutation(id, async |tx| {
            apply(tx, self.pool.schema(), id, &write, limits).await
        })
    }
}

struct Part<'a> {
    tx: &'a Transaction<'a>,
    schema: &'a str,
    id: &'a str,
}

impl Part<'_> {
    fn sql(&self, template: &'static str) -> String {
        template.replace("{{schema}}", self.schema)
    }

    async fn repeats(&self, owner: i64, next: u64, records: &[TurnRecord]) -> Result<()> {
        let Some(first) = records.first() else {
            return Ok(());
        };
        let from = i32::try_from(first.seq).map_err(|_| corrupt(self.id))?;
        let end =
            i32::try_from(records.last().expect("nonempty").seq).map_err(|_| corrupt(self.id))?;
        let statement = self.sql(sql!("records/write_digests"));
        let params: [&(dyn tokio_postgres::types::ToSql + Sync); 4] =
            [&self.id, &owner, &from, &end];
        let rows = self.tx.query_raw(&statement, params).await?;
        futures_util::pin_mut!(rows);
        for record in records {
            let bad = || {
                Refused::format_unsupported(
                    self.id,
                    &format!("record {} is unreadable", record.seq),
                )
            };
            let row = rows.try_next().await?.ok_or_else(bad)?;
            let body: Value = row.try_get("body")?;
            let stored =
                super::record_read::decode_record(&row, record.seq, &body).ok_or_else(bad)?;
            if stored != *record {
                return Err(conflict(
                    self.id,
                    "draft_mismatch",
                    json!({"next_seq":next,"seq":record.seq}),
                )
                .into());
            }
        }
        Ok(())
    }

    async fn sealed(&self, write: &Write) -> Result<Option<Written>> {
        let query = self.sql(sql!("records/write_sealed"));
        let Some(row) = self.tx.query_opt(&query, &[&self.id, &write.draft]).await? else {
            return Ok(None);
        };
        let value = values::row(&row).map_err(Error::from)?;
        if value["neosian_format"] != 2
            || value["valid"] != true
            || value["records"].as_u64().is_none_or(|n| n == 0)
        {
            return Err(Refused::format_unsupported(
                self.id,
                &format!("turn {}: its record header is unreadable", value["turn"]),
            )
            .into());
        }
        let created_at = draft::timestamp(&value, self.id)?;
        let header = Header {
            turn: value["turn"].as_u64().ok_or_else(|| corrupt(self.id))?,
            created_at,
            actor: row.try_get("actor")?,
            redacted: false,
            records: value["records"].as_u64().ok_or_else(|| corrupt(self.id))?,
            bytes: value["bytes"].as_u64().ok_or_else(|| corrupt(self.id))?,
        };
        if write.discard
            || write
                .records
                .last()
                .is_some_and(|r| r.seq >= header.records)
        {
            return Err(conflict(self.id, "draft_sealed", json!({"turn":header.to_json()})).into());
        }
        actor(self.id, write, header.actor.as_deref())?;
        if write.turn_bytes.is_some_and(|n| n != header.bytes) {
            return Err(pinned_size(self.id, write.turn_bytes, header.bytes).into());
        }
        self.repeats(row.try_get("owner")?, header.records, &write.records)
            .await?;
        let mut done = answer(write, State::Sealed, header.records);
        done.turn = Some(header);
        Ok(Some(done))
    }
}

async fn apply(
    tx: &Transaction<'_>,
    schema: &str,
    id: &str,
    write: &Write,
    limits: &Records,
) -> Result<Written> {
    let part = Part { tx, schema, id };
    if let Some(done) = part.sealed(write).await? {
        return Ok(done);
    }
    let sizes = Sizes::new(id, &write.draft);
    let mut stored = 0;
    let mut freed = 0;
    let mut state = match part.load(&write.draft).await? {
        Some(state) if write.discard => {
            let query = part.sql(sql!("records/write_discard"));
            tx.execute(&query, &[&id, &state.owner]).await?;
            let mut removed = answer(write, State::Discarded, state.check.next_seq());
            removed.freed_bytes = state.bytes;
            return Ok(removed);
        }
        Some(state) => {
            actor(id, write, state.actor.as_deref())?;
            if write.turn_bytes.is_some() && write.turn_bytes != state.stated {
                return Err(pinned_size(id, write.turn_bytes, state.check.bytes()).into());
            }
            state
        }
        None if write.discard || (write.records.is_empty() && !write.seal) => {
            return Ok(answer(write, State::None, 0));
        }
        None if write.records.is_empty() => {
            return Err(
                Error::Value("a seal needs at least one record in the draft".into()).into(),
            );
        }
        None => {
            let (state, gone) = part.open(write, limits, &sizes).await?;
            stored = sizes.bookkeeping();
            freed = gone;
            state
        }
    };
    let next = state.check.next_seq();
    if write.records.first().is_some_and(|r| r.seq > next) {
        return Err(conflict(id, "draft_gap", json!({"next_seq":next})).into());
    }
    let repeats = write.records.partition_point(|record| record.seq < next);
    part.repeats(state.owner, next, &write.records[..repeats])
        .await?;
    // Encode each accepted row into the bound part, retaining only one JSON tree.
    let mut fresh = vec![b'['];
    for record in &write.records[repeats..] {
        state
            .check
            .admit(record, state.stated, limits.max_record_turn_bytes)
            .map_err(|r| match r {
                Refusal::Invalid(why) => Error::Value(why),
                Refusal::Stated { stated, bytes } => pinned_size(id, Some(stated), bytes).into(),
                Refusal::Bound { bytes, maximum } => Error::Value(size_refusal(bytes, maximum)),
            })?;
        let row = physical_row(record, state.check.piece_complete());
        if fresh.len() > 1 {
            fresh.push(b',');
        }
        serde_json::to_writer(&mut fresh, &row).expect("a JSON value encodes");
        let cost = sizes.record(record);
        stored += cost;
        state.bytes = state.bytes.checked_add(cost).ok_or_else(|| corrupt(id))?;
    }
    if fresh.len() > 1 {
        fresh.push(b']');
        let rows = std::str::from_utf8(&fresh).expect("JSON is UTF-8");
        let query = part.sql(sql!("records/write_rows"));
        tx.execute(&query, &[&id, &state.owner, &rows, &0_i32])
            .await?;
    }
    let next = state.check.next_seq();
    let mut done;
    if write.seal {
        state.check.sealable().map_err(Error::Value)?;
        let bytes = state.check.bytes();
        if bytes > limits.max_record_turn_bytes {
            return Err(Error::Value(size_refusal(bytes, limits.max_record_turn_bytes)).into());
        }
        if state.stated.is_some_and(|stated| stated != bytes) {
            return Err(pinned_size(id, state.stated, bytes).into());
        }
        let (header, created) = part.seal(write, &state).await?;
        stored += sizes.header(header.actor.as_deref());
        if created {
            stored += sizes.conversation_row();
        }
        freed += sizes.bookkeeping();
        done = answer(write, State::Sealed, next);
        done.turn = Some(header);
    } else {
        part.save(&state).await?;
        done = answer(write, State::Open, next);
    }
    (done.stored_bytes, done.freed_bytes) = (stored, freed);
    Ok(done)
}
