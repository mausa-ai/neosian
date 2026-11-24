//! PostgreSQL's draft bookkeeping row and its publication into two headers.
use serde_json::{Value, json};

use super::{Part, Result, corrupt};
use crate::conversation::turn_records::{DraftCheck, Header, Sizes, Write, conflict, size_refusal};
use crate::conversation::{Error, Refused};
use crate::limits::Records;
use crate::postgres::{sql, values};
use crate::store::time::Stamp;

pub(super) struct Draft {
    pub owner: i64,
    pub actor: Option<String>,
    pub stated: Option<u64>,
    pub check: DraftCheck,
    pub bytes: u64,
}

pub(super) fn timestamp(fields: &Value, id: &str) -> Result<Stamp> {
    fields["created_at"]
        .as_str()
        .and_then(|s| Stamp::parse(s).ok())
        .ok_or_else(|| corrupt(id))
}

impl Part<'_> {
    pub(super) async fn load(&self, name: &str) -> Result<Option<Draft>> {
        let query = self.sql(sql!("records/write_state"));
        let Some(row) = self.tx.query_opt(&query, &[&self.id, &name]).await? else {
            return Ok(None);
        };
        let bytes: i64 = row.try_get("bytes")?;
        let stated: Option<i64> = row.try_get("turn_bytes")?;
        let position: Value = row.try_get("position")?;
        Ok(Some(Draft {
            owner: row.try_get("owner")?,
            actor: row.try_get("actor")?,
            stated: stated
                .map(u64::try_from)
                .transpose()
                .map_err(|_| corrupt(self.id))?,
            check: serde_json::from_value(position).map_err(|_| corrupt(self.id))?,
            bytes: u64::try_from(bytes).map_err(|_| corrupt(self.id))?,
        }))
    }

    pub(super) async fn open(
        &self,
        write: &Write,
        limits: &Records,
        sizes: &Sizes,
    ) -> Result<(Draft, u64)> {
        let check = DraftCheck::default();
        let position = json!(check);
        let bytes = sizes.bookkeeping();
        let stated = write.turn_bytes.and_then(|n| i64::try_from(n).ok());
        let seconds = limits.draft_seconds as i64;
        let query = self.sql(sql!("records/write_open"));
        let row = self
            .tx
            .query_one(
                &query,
                &[
                    &self.id,
                    &write.draft,
                    &seconds,
                    &write.actor,
                    &stated,
                    &position,
                    &(bytes as i64),
                ],
            )
            .await?;
        let owner: Option<i64> = row.try_get("owner")?;
        let owner = owner.ok_or_else(|| conflict(self.id, "drafts_open", json!({"limit":16})))?;
        if let Some(n) = write
            .turn_bytes
            .filter(|n| *n > limits.max_record_turn_bytes)
        {
            return Err(Error::Value(size_refusal(n, limits.max_record_turn_bytes)).into());
        }
        let freed: i64 = row.try_get("freed")?;
        let freed = u64::try_from(freed).map_err(|_| corrupt(self.id))?;
        Ok((
            Draft {
                owner,
                actor: write.actor.clone(),
                stated: write.turn_bytes,
                check,
                bytes,
            },
            freed,
        ))
    }

    pub(super) async fn save(&self, state: &Draft) -> Result<()> {
        let bytes = i64::try_from(state.bytes).map_err(|_| corrupt(self.id))?;
        let query = self.sql(sql!("records/write_update"));
        self.tx
            .execute(
                &query,
                &[&self.id, &state.owner, &json!(state.check), &bytes],
            )
            .await?;
        Ok(())
    }

    pub(super) async fn seal(&self, write: &Write, state: &Draft) -> Result<(Header, bool)> {
        let records = i32::try_from(state.check.next_seq()).map_err(|_| {
            Refused::format_unsupported(
                self.id,
                "the draft holds more records than Postgres can number",
            )
        })?;
        let bytes = i64::try_from(state.check.bytes()).map_err(|_| corrupt(self.id))?;
        let query = self.sql(sql!("records/write_seal"));
        let row = self
            .tx
            .query_opt(
                &query,
                &[
                    &self.id,
                    &state.owner,
                    &state.actor,
                    &write.draft,
                    &records,
                    &bytes,
                ],
            )
            .await?
            .ok_or_else(|| {
                Refused::format_unsupported(
                    self.id,
                    "the log has reached the last turn a conversation can hold",
                )
            })?;
        let number: i32 = row.try_get("turn")?;
        let header = Header {
            turn: number as u64,
            created_at: timestamp(&values::row(&row).map_err(Error::from)?, self.id)?,
            actor: state.actor.clone(),
            redacted: false,
            records: records as u64,
            bytes: bytes as u64,
        };
        Ok((header, row.try_get("created")?))
    }
}
