//! Confirm a sealed write using its original mapped records or retained digests.
use super::{Part, Result, actor, answer, corrupt, draft, pinned_size};
use crate::conversation::turn_records::{Header, State, Write, Written, conflict};
use crate::conversation::{Error, Refused};
use crate::postgres::{record_seal, sql, values};
use serde_json::json;

impl Part<'_> {
    pub(super) async fn sealed(&self, write: &Write) -> Result<Option<Written>> {
        let receipt = if self.fold_receipts {
            self.tx
                .query_opt(
                    &self.sql(sql!("records/seal_find")),
                    &[&self.id, &write.draft],
                )
                .await?
        } else {
            None
        };
        let (row, folded) = match receipt {
            Some(row) => (row, true),
            None => {
                let Some(row) = self
                    .tx
                    .query_opt(
                        &self.sql(sql!("records/write_sealed")),
                        &[&self.id, &write.draft],
                    )
                    .await?
                else {
                    return Ok(None);
                };
                (row, false)
            }
        };
        let value = values::row(&row).map_err(Error::from)?;
        if (!folded && value["neosian_format"] != 2)
            || value["valid"] != true
            || value["records"].as_u64().is_none_or(|n| n == 0)
        {
            let reason = if folded {
                "its folded receipt is unreadable"
            } else {
                "its record header is unreadable"
            };
            return Err(Refused::format_unsupported(
                self.id,
                &format!("turn {}: {reason}", value["turn"]),
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
        if folded {
            record_seal::confirm_range(
                self.tx,
                self.schema,
                self.id,
                row.try_get("turn")?,
                header.records,
                &write.records,
            )
            .await?;
        } else {
            self.repeats(row.try_get("owner")?, header.records, &write.records)
                .await?;
        }
        let mut done = answer(write, State::Sealed, header.records);
        done.turn = Some(header);
        Ok(Some(done))
    }
}
