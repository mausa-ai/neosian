//! Fold a conversation in one transaction. Its ordered header portal only
//! brings one turn into memory, including when validating older whole rows.
use serde_json::Value;
use tokio_postgres::{Row, Statement, Transaction};

use super::connection::Failure;
use super::{PostgresStore, record_read, record_seal, sql, values};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::message;
use crate::conversation::search::messages_text;
use crate::conversation::turn_records::migration::Folded;
use crate::conversation::turn_records::{Header, check_draft_id};
use crate::conversation::{Error, Refused};
use crate::{limits, remote::codec};

struct Fold<'a> {
    schema: &'a str,
    id: &'a str,
    maximum: u64,
    budget: u64,
    fold_receipts: bool,
}

impl PostgresStore {
    pub fn fold_records(
        &self,
        id: &str,
        discard_pending: bool,
        limits: &limits::Records,
    ) -> Result<Folded, Error> {
        parse_conversation_id(id)?;
        self.require_records()?;
        let fold = Fold {
            schema: self.pool.schema(),
            fold_receipts: self.supports_fold_receipts()?,
            id,
            maximum: limits.max_turn_bytes,
            budget: limits::response_bytes()?,
        };
        self.pool
            .record_mutation(id, async |tx| fold.run(tx, discard_pending).await)
    }
}

impl Fold<'_> {
    fn sql(&self, template: &str) -> String {
        template.replace("{{schema}}", self.schema)
    }

    fn damaged(&self, number: i32, why: &str) -> Failure {
        Refused::format_unsupported(self.id, &format!("turn row {number} {why}")).into()
    }

    async fn run(&self, tx: &Transaction<'_>, discard: bool) -> Result<Folded, Failure> {
        let pending = tx
            .query_one(&self.sql(sql!("records/fold_pending")), &[&self.id])
            .await?;
        let drafts: i64 = pending.try_get("drafts")?;
        let restores: i64 = pending.try_get("restores")?;
        if !discard && (drafts > 0 || restores > 0) {
            return Err(Error::Value(format!(
                "conversation {} holds pending work ({drafts} open drafts, {restores} staged restores); \
                 let it finish or pass --discard-pending to remove it; nothing in this conversation was folded",
                self.id
            )).into());
        }
        if !pending.try_get::<_, bool>("valid")? {
            return Err(Refused::format_unsupported(
                self.id,
                "a record mapping has no published turn or names an open owner",
            )
            .into());
        }
        let mut found = Folded {
            conversation_id: self.id.into(),
            drafts: drafts as u64,
            restores: restores as u64,
            ..Default::default()
        };
        let headers = tx
            .bind(&self.sql(sql!("records/fold_headers")), &[&self.id])
            .await?;
        let range = tx.prepare(&self.sql(sql!("records/read_range"))).await?;
        let overflow = tx.prepare(&self.sql(sql!("records/read_overflow"))).await?;
        let legacy = tx.prepare(&self.sql(sql!("records/read_legacy"))).await?;
        let publish = tx.prepare(&self.sql(sql!("records/fold_publish"))).await?;
        let bodies = record_read::Bodies {
            tx,
            range: &range,
            overflow: &overflow,
            id: self.id,
            budget: self.budget,
        };
        while let Some(row) = tx.query_portal(&headers, 1).await?.first() {
            let number = row.try_get("turn")?;
            let meta = values::row(row).map_err(Error::from)?;
            values::fits(&meta, self.budget).map_err(Error::from)?;
            if number < 1 {
                return Err(self.damaged(number, "has an invalid number"));
            }
            match row.try_get::<_, i32>("neosian_format")? {
                1 => {
                    if !meta["owner"].is_null() {
                        return Err(self.damaged(number, "has an unexpected record mapping"));
                    }
                    if meta["redacted"] == true {
                        if meta["empty"] != true {
                            return Err(self.damaged(number, "is redacted yet carries messages"));
                        }
                        found.skeletons += 1;
                    } else {
                        if meta["array_body"] != true || meta["empty"] == true {
                            return Err(self.damaged(number, "has no message array"));
                        }
                        self.check_legacy(tx, &legacy, number).await?;
                        found.whole += 1;
                    }
                }
                2 => {
                    self.convert(&bodies, &publish, number, row, &meta).await?;
                    found.folded += 1;
                }
                format => {
                    return Err(
                        self.damaged(number, &format!("declares unsupported format {format}"))
                    );
                }
            }
        }
        if drafts > 0 || restores > 0 {
            let result = tx
                .query_one(&self.sql(sql!("records/fold_discard")), &[&self.id])
                .await?;
            if drafts != result.try_get::<_, i64>("drafts")?
                || restores != result.try_get::<_, i64>("restores")?
                || !result.try_get::<_, bool>("complete")?
            {
                return Err(Refused::format_unsupported(
                    self.id,
                    "could not discard all pending work during fold",
                )
                .into());
            }
        }
        Ok(found)
    }

    async fn check_legacy(
        &self,
        tx: &Transaction<'_>,
        legacy: &Statement,
        number: i32,
    ) -> Result<(), Failure> {
        let mut raw =
            record_read::legacy_body(tx, legacy, self.id, number as u64, self.budget).await?;
        let messages = match serde_json::from_slice::<Vec<Value>>(&raw) {
            Ok(messages) => messages,
            Err(_) => {
                // PostgreSQL JSONB permits numbers outside the Rust codec range.
                // Preserve their numeric type and truthiness in the validation
                // copy only. All other parser and message errors still refuse.
                approximate_large_numbers(&mut raw);
                serde_json::from_slice(&raw)
                    .map_err(|_| self.damaged(number, "has unreadable legacy messages"))?
            }
        };
        drop(raw);
        for item in messages {
            message::from_json(&item)
                .map_err(|_| self.damaged(number, "has an undecodable message"))?;
        }
        Ok(())
    }

    async fn convert(
        &self,
        bodies: &record_read::Bodies<'_, '_>,
        publish: &Statement,
        number: i32,
        row: &Row,
        meta: &Value,
    ) -> Result<(), Failure> {
        let invalid = || self.damaged(number, "has a missing or invalid record mapping");
        let owner = meta["owner"]
            .as_i64()
            .filter(|&n| n > 0)
            .ok_or_else(invalid)?;
        let part = row
            .try_get::<_, Option<i32>>("part")?
            .filter(|&n| n == 0 || n == number)
            .ok_or_else(invalid)?;
        let records = meta["records"]
            .as_u64()
            .filter(|&n| n > 0)
            .ok_or_else(invalid)?;
        let bytes = meta["bytes"].as_u64().ok_or_else(invalid)?;
        if meta["dates_match"] != true
            || meta["redacted"] != false
            || meta["empty"] != true
            || !meta["draft"]
                .as_str()
                .is_some_and(|name| check_draft_id(name).is_ok())
        {
            return Err(invalid());
        }
        if bytes > self.maximum {
            return Err(Error::Value(format!(
                "resource limit {} exceeded: turn {number} of {} holds {bytes} > {} bytes; \
                 raise the setting to fold it; nothing in this conversation was folded",
                limits::TURN_BYTES,
                self.id,
                self.maximum
            ))
            .into());
        }
        let header = Header {
            turn: number as u64,
            created_at: codec::moment(meta, "created_at").map_err(Error::from)?,
            actor: row.try_get("actor")?,
            redacted: false,
            records,
            bytes,
        };
        let turn = bodies.assemble(header).await?;
        if part == 0 {
            if !self.fold_receipts {
                return Err(Error::Value(format!(
                    "turn {number} of {} has a native seal whose original record digests must be retained; \
                     install Postgres records extension 2 to fold it; nothing in this conversation was folded",
                    self.id
                )).into());
            }
            record_seal::retain(bodies.tx, self.schema, self.id, number, owner).await?;
        }
        let search = messages_text(&turn.messages);
        let messages = Value::Array(turn.messages.iter().map(message::to_json).collect());
        drop(turn);
        let changed = bodies
            .tx
            .query_one(
                publish,
                &[&self.id, &number, &owner, &part, &messages, &search],
            )
            .await?;
        if changed.try_get::<_, i64>("published")? != 1
            || changed.try_get::<_, i64>("unlinked")? != 1
            || changed.try_get::<_, i64>("removed")? != records as i64
            || !changed.try_get::<_, bool>("valid")?
        {
            return Err(self.damaged(
                number,
                "could not publish its whole turn and retire its exact record part",
            ));
        }
        Ok(())
    }
}

/// The database supplied valid JSON. Skip quoted strings, including escaped
/// quotes, and replace only numeric tokens that serde_json cannot represent.
fn approximate_large_numbers(json: &mut [u8]) {
    let mut quoted = false;
    let mut pos = 0;
    while let Some(&ch) = json.get(pos) {
        match ch {
            b'\\' if quoted => pos += 2,
            b'"' => {
                quoted = !quoted;
                pos += 1;
            }
            b'-' | b'0'..=b'9' if !quoted => {
                let first = pos;
                pos += 1;
                while matches!(
                    json.get(pos),
                    Some(b'0'..=b'9' | b'.' | b'e' | b'E' | b'+' | b'-')
                ) {
                    pos += 1;
                }
                let token = &mut json[first..pos];
                if serde_json::from_slice::<serde_json::Number>(token).is_err() {
                    token.fill(b' ');
                    token[0] = b'1';
                }
            }
            _ => pos += 1,
        }
    }
}
