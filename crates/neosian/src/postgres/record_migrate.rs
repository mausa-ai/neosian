//! Online conversion of legacy turns. Each turn is locked and published in
//! its own transaction; an interrupted run resumes by recognizing its headers.
use serde_json::Value;
use tokio_postgres::Transaction;

use super::connection::Failure;
use super::{PostgresStore, record_read, record_write, sql, values};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::message::{self, Message};
use crate::conversation::search::messages_text;
use crate::conversation::turn_records::migration::{Migrated, carried, migration_id};
use crate::conversation::turn_records::{TurnRecord, messages_of, records_of};
use crate::conversation::{Error, Refused};
use crate::limits;

enum Outcome {
    Migrated,
    Records,
    Skeleton,
    Spelling,
    Content,
    Size,
    Number,
}

struct Migration<'a> {
    schema: &'a str,
    id: &'a str,
    label: String,
    budget: u64,
    maximum: u64,
}

impl PostgresStore {
    pub fn migrate_records(&self, id: &str, limits: &limits::Records) -> Result<Migrated, Error> {
        parse_conversation_id(id)?;
        self.require_records()?;
        let run = Migration {
            schema: self.pool.schema(),
            id,
            label: migration_id(id),
            budget: limits::response_bytes()?,
            maximum: limits.max_record_turn_bytes,
        };
        let high = self.last_turn_number(id)? as i64;
        let mut after = i64::from(i32::MIN) - 1;
        let mut found = Migrated {
            conversation_id: id.to_owned(),
            ..Default::default()
        };
        while after < high {
            let row = self
                .pool
                .record_mutation(id, async |tx| run.next(tx, after, high).await)?;
            let Some((number, outcome)) = row else { break };
            after = i64::from(number);
            match outcome {
                Outcome::Migrated => found.migrated += 1,
                Outcome::Records => found.records += 1,
                Outcome::Skeleton => found.skeletons += 1,
                Outcome::Spelling => found.kept.spelling += 1,
                Outcome::Content => found.kept.content += 1,
                Outcome::Size => found.kept.size += 1,
                Outcome::Number => found.kept.number += 1,
            }
        }
        Ok(found)
    }
}

impl Migration<'_> {
    fn sql(&self, template: &str) -> String {
        template.replace("{{schema}}", self.schema)
    }

    fn damaged(&self, number: i32, why: &str) -> Failure {
        Refused::format_unsupported(self.id, &format!("turn row {number} {why}")).into()
    }

    async fn next(
        &self,
        tx: &Transaction<'_>,
        after: i64,
        high: i64,
    ) -> Result<Option<(i32, Outcome)>, Failure> {
        let query = self.sql(sql!("records/migrate_next"));
        let Some(row) = tx.query_opt(&query, &[&self.id, &after, &high]).await? else {
            return Ok(None);
        };
        let header = values::row(&row).map_err(Error::from)?;
        values::fits(&header, self.budget).map_err(Error::from)?;
        let number: i32 = row.try_get("turn")?;
        if number < 1 {
            return Err(self.damaged(number, "has an invalid number"));
        }
        let mapped: bool = row.try_get("mapped")?;
        let outcome = match row.try_get::<_, i32>("neosian_format")? {
            1 if header["redacted"] == true => {
                if header["empty"] != true {
                    return Err(self.damaged(number, "is redacted yet carries messages"));
                }
                Outcome::Skeleton
            }
            1 => self.convert(tx, number, mapped).await?,
            2 => {
                if header["valid"] != true || header["empty"] != true || header["redacted"] != false
                {
                    return Err(self.damaged(number, "has a missing or invalid record mapping"));
                }
                Outcome::Records
            }
            version => {
                return Err(self.damaged(number, &format!("declares unsupported format {version}")));
            }
        };
        Ok(Some((number, outcome)))
    }

    async fn convert(
        &self,
        tx: &Transaction<'_>,
        number: i32,
        mapped: bool,
    ) -> Result<Outcome, Failure> {
        let statement = tx.prepare(&self.sql(sql!("records/read_legacy"))).await?;
        let body =
            record_read::legacy_body(tx, &statement, self.id, number as u64, self.budget).await?;
        // JSONB is valid JSON, but can hold numbers beyond the Rust codec's range.
        let Ok(source) = serde_json::from_slice::<Value>(&body) else {
            return Ok(Outcome::Content);
        };
        drop(body);
        let Some(items) = source.as_array().filter(|items| !items.is_empty()) else {
            return Err(self.damaged(number, "has no message array"));
        };
        let messages: Vec<Message> = items
            .iter()
            .map(message::from_json)
            .collect::<Result<_, _>>()
            .map_err(|_| self.damaged(number, "has an undecodable message"))?;
        let rendered = Value::Array(messages.iter().map(message::to_json).collect());
        if !carried(&source, &rendered) {
            return Ok(Outcome::Content);
        }
        let comparison = tx
            .query_one(
                &self.sql(sql!("records/migrate_compare")),
                &[&self.id, &number, &rendered, &messages_text(&messages)],
            )
            .await?;
        let checks = values::row(&comparison).map_err(Error::from)?;
        values::fits(&checks, self.budget).map_err(Error::from)?;
        if checks["equal_search"] != true {
            return Ok(Outcome::Content);
        }
        if checks["equal_value"] != true {
            if source == rendered {
                return Ok(Outcome::Content);
            }
            let raw = tx
                .query_one(
                    &self.sql(sql!("records/migrate_source_value")),
                    &[&self.id, &number, &source],
                )
                .await?;
            return Ok(if raw.try_get::<_, bool>("preserved")? {
                Outcome::Spelling
            } else {
                Outcome::Content
            });
        }
        drop(source);
        if checks["equal_spelling"] != true {
            return Ok(Outcome::Spelling);
        }
        let Ok(records) = records_of(&messages) else {
            return Ok(Outcome::Content);
        };
        drop(messages);
        let Ok(rebuilt) = messages_of(&records) else {
            return Ok(Outcome::Content);
        };
        let original = rendered.as_array().expect("rendered messages");
        if rebuilt.len() != original.len()
            || rebuilt
                .iter()
                .zip(original)
                .any(|(m, v)| message::to_json(m) != *v)
        {
            return Ok(Outcome::Content);
        }
        drop(rebuilt);
        drop(rendered);
        let bytes = records.iter().map(|r| r.text.len() as u64).sum::<u64>();
        if bytes > self.maximum {
            return Ok(Outcome::Size);
        }
        if mapped {
            return Ok(Outcome::Number);
        }
        self.publish(tx, number, &records, bytes).await?;
        Ok(Outcome::Migrated)
    }

    async fn publish(
        &self,
        tx: &Transaction<'_>,
        number: i32,
        records: &[TurnRecord],
        bytes: u64,
    ) -> Result<(), Failure> {
        let row = tx
            .query_one(&self.sql(sql!("records/migrate_owner")), &[&self.id])
            .await?;
        let owner: i64 = row.try_get("owner")?;
        let insert = tx.prepare(&self.sql(sql!("records/write_rows"))).await?;
        // Each row is already bounded by the record splitter. Flush at a
        // modest byte threshold so a large turn never becomes one SQL batch.
        let mut batch = vec![b'['];
        let mut batch_records = 0;
        for (i, record) in records.iter().enumerate() {
            if batch.len() > 1 {
                batch.push(b',')
            }
            let last = records.get(i + 1).is_none_or(|next| next.chunk == 0);
            serde_json::to_writer(&mut batch, &record_write::physical_row(record, last))
                .expect("a JSON value encodes");
            batch_records += 1;
            if batch.len() >= 64 * 1024 || i + 1 == records.len() {
                batch.push(b']');
                let rows = std::str::from_utf8(&batch).expect("JSON is UTF-8");
                let inserted = tx
                    .execute(&insert, &[&self.id, &owner, &rows, &number])
                    .await?;
                if inserted != batch_records {
                    return Err(self.damaged(number, "could not store all of its records"));
                }
                batch.truncate(1);
                batch_records = 0;
            }
        }
        let count = i32::try_from(records.len())
            .map_err(|_| self.damaged(number, "has too many records"))?;
        let mapped = tx
            .execute(
                &self.sql(sql!("records/migrate_publish")),
                &[
                    &self.id,
                    &number,
                    &owner,
                    &self.label,
                    &count,
                    &(bytes as i64),
                ],
            )
            .await?;
        if mapped != 1 {
            return Err(self.damaged(number, "could not publish its record mapping"));
        }
        Ok(())
    }
}
