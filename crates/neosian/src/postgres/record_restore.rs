//! Stage and publish a restore in extension 1. Publication makes the
//! records visible by mapping each source turn to its original part.
mod items;
mod publish;

use super::connection::Failure;
use super::{PostgresStore, sql};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::turn_records::{
    Position, Restore, RestoreReceipt, RestoreStream, Restored, Sizes, Staged, conflict,
};
use crate::conversation::{Error, Refused};
use crate::limits::Records;
use serde_json::{Value, json};
use tokio_postgres::Transaction;

type Result<T> = std::result::Result<T, Failure>;

fn reason(id: &str, why: &str, position: Position) -> Failure {
    conflict(id, why, json!({"position": position.to_json()})).into()
}

fn corrupt(id: &str) -> Failure {
    Refused::format_unsupported(id, "a restore's stored state is unreadable").into()
}

impl PostgresStore {
    pub fn restore_records(
        &self,
        id: &str,
        restore: Restore,
        limits: &Records,
    ) -> std::result::Result<Restored, Error> {
        restore.check(id).map_err(Error::Value)?;
        parse_conversation_id(id)?;
        self.require_records()?;
        self.pool.record_mutation(id, async |tx| {
            let part = Part {
                tx,
                schema: self.pool.schema(),
                id,
            };
            part.apply(&restore, limits).await
        })
    }
}

struct Part<'a, 'tx> {
    tx: &'a Transaction<'tx>,
    schema: &'a str,
    id: &'a str,
}

impl Part<'_, '_> {
    fn sql(&self, template: &str) -> String {
        template.replace("{{schema}}", self.schema)
    }

    async fn discard(&self, owners: &[i64]) -> Result<u64> {
        let query = self.sql(sql!("records/restore_discard"));
        let row = self.tx.query_one(&query, &[&self.id, &owners]).await?;
        let freed: i64 = row.try_get("freed")?;
        u64::try_from(freed).map_err(|_| corrupt(self.id))
    }

    /// Opening is private to this transaction. Stale owners are removed
    /// only after the new stream and all its repeated items are validated.
    async fn open(
        &self,
        name: &str,
        seconds: u64,
        stream: &RestoreStream,
        bytes: u64,
    ) -> Result<(i64, Vec<i64>)> {
        let query = self.sql(sql!("records/restore_open"));
        let seconds = i64::try_from(seconds).unwrap_or(i64::MAX);
        let bytes = i64::try_from(bytes).map_err(|_| corrupt(self.id))?;
        let row = self
            .tx
            .query_one(&query, &[&self.id, &name, &seconds, &json!(stream), &bytes])
            .await?;
        if row.try_get("occupied")? {
            return Err(Refused::conflict(self.id, "target_occupied").into());
        }
        if let Some(competing) = row.try_get::<_, Option<String>>("competing")? {
            return Err(conflict(self.id, "restore_open", json!({"restore": competing})).into());
        }
        Ok((row.try_get("owner")?, row.try_get("expired")?))
    }

    async fn apply(&self, restore: &Restore, limits: &Records) -> Result<Restored> {
        let name = restore.restore.as_str();
        let query = self.sql(sql!("records/restore_state"));
        let found = self.tx.query_opt(&query, &[&self.id, &name]).await?;
        let mut opening_cost = 0;
        let (owner, mut stream, held, expired) = if let Some(row) = found {
            let raw: Value = row.try_get("position")?;
            match row.try_get::<_, &str>("state")? {
                "committed" => {
                    let receipt: RestoreReceipt =
                        serde_json::from_value(raw).map_err(|_| corrupt(self.id))?;
                    return Ok(receipt.replay(self.id, restore)?);
                }
                "open" => {}
                _ => return Err(corrupt(self.id)),
            }
            let owner: i64 = row.try_get("owner")?;
            let stream = serde_json::from_value::<RestoreStream>(raw);
            if restore.discard {
                let position = stream.ok().map(|s| s.position());
                let mut gone = Restored::new(name, Staged::Discarded, position);
                gone.freed_bytes = self.discard(&[owner]).await?;
                return Ok(gone);
            }
            let stream = stream.map_err(|_| corrupt(self.id))?;
            if restore.skips(stream.position()) {
                return Err(reason(self.id, "restore_gap", stream.position()));
            }
            if restore.is_probe() {
                return Ok(Restored::new(name, Staged::Open, Some(stream.position())));
            }
            let bytes: i64 = row.try_get("bytes")?;
            let held = u64::try_from(bytes).map_err(|_| corrupt(self.id))?;
            (owner, stream, held, Vec::new())
        } else {
            if restore.skips(Position::default()) {
                return Err(reason(self.id, "restore_gap", Position::default()));
            }
            if restore.discard || restore.is_probe() {
                return Ok(Restored::new(name, Staged::None, None));
            }
            opening_cost = Sizes::new(self.id, name).bookkeeping();
            let stream = RestoreStream::default();
            let (owner, expired) = self
                .open(name, limits.draft_seconds, &stream, opening_cost)
                .await?;
            (owner, stream, opening_cost, expired)
        };

        let before = stream.position();
        let added = items::stage(
            self,
            owner,
            restore,
            &mut stream,
            limits.max_record_turn_bytes,
        )
        .await?;
        let total = held
            .checked_add(added)
            .and_then(|n| i64::try_from(n).ok())
            .ok_or_else(|| corrupt(self.id))?;
        let mut done = if restore.commit {
            self.publish(owner, restore, before, &stream).await?
        } else {
            let query = self.sql(sql!("records/write_update"));
            self.tx
                .execute(&query, &[&self.id, &owner, &json!(stream), &total])
                .await?;
            Restored::new(name, Staged::Open, Some(stream.position()))
        };
        done.stored_bytes += opening_cost + added;
        if !expired.is_empty() {
            done.freed_bytes = done
                .freed_bytes
                .checked_add(self.discard(&expired).await?)
                .ok_or_else(|| corrupt(self.id))?;
        }
        Ok(done)
    }
}
