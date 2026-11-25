//! Mixed format-1 and format-2 turns on an enabled database. The format-2
//! header is small; its mapping names exactly one primary-key record range.
mod history;
mod legacy;
mod page;
mod selected;
#[cfg(test)]
mod tests;

pub(super) use history::{history_facts, history_hit};
pub(super) use legacy::legacy_body;
pub(super) use selected::{one_record, whole_at};

use super::{PostgresStore, connection::failure, rows, values};
use crate::conversation::turn_records::{Header, TurnRecord, messages_of};
use crate::conversation::{Error, Refused, Turn, message};
use crate::limits;
use crate::remote::codec;
use futures_util::TryStreamExt;
use serde_json::{Value, json};
use tokio_postgres::{Portal, Statement, Transaction};

pub(super) struct Statements {
    pub(super) headers: Statement,
    pub(super) legacy: Statement,
    pub(super) head_scan: Statement,
    pub(super) overflow: Statement,
    pub(super) range: Statement,
    pub(super) piece: Statement,
}

enum Stored {
    Whole(Turn),
    Records(Header),
}

fn signed(number: u64) -> i64 {
    i64::try_from(number).unwrap_or(i64::MAX)
}

fn damaged(id: &str, turn: u64, why: &str) -> Error {
    Refused::format_unsupported(id, &format!("turn {turn}: {why}")).into()
}

/// Validate the stored shape, position and canonical digest for both reads
/// and retries. The caller decoded the body once to apply its row guard.
pub(super) fn decode_record(
    row: &tokio_postgres::Row,
    expected_seq: u64,
    body: &Value,
) -> Option<TurnRecord> {
    let format: i32 = row.try_get("neosian_format").ok()?;
    let digest: &[u8] = row.try_get("digest").ok()?;
    let (_, record) = TurnRecord::from_json(body).ok()?;
    (format == 1 && record.seq == expected_seq && record.digest(None) == digest).then_some(record)
}

struct Reader<'a, 'c> {
    tx: &'a Transaction<'c>,
    statements: &'a Statements,
    id: &'a str,
    budget: u64,
}

impl Reader<'_, '_> {
    async fn headers(
        &self,
        first: u64,
        last: u64,
        limit: Option<i64>,
        project_legacy: bool,
    ) -> Result<Portal, Error> {
        self.tx
            .bind(
                &self.statements.headers,
                &[
                    &self.id,
                    &signed(first),
                    &signed(last),
                    &limit,
                    &project_legacy,
                ],
            )
            .await
            .map_err(failure)
            .map_err(Into::into)
    }

    async fn next_header(&self, portal: &Portal) -> Result<Option<Stored>, Error> {
        self.next_header_in(portal, false).await
    }

    async fn next_header_in(
        &self,
        portal: &Portal,
        search_hit: bool,
    ) -> Result<Option<Stored>, Error> {
        // A legacy row can hold an entire conversation turn. Bring at most
        // one such row into memory, even when a caller stops after that row.
        let found = self.tx.query_portal(portal, 1).await.map_err(failure)?;
        let Some(row) = found.first() else {
            return Ok(None);
        };
        let mut row = values::row(row)?;
        let fields = row.as_object_mut().expect("database row is an object");
        let records = fields.remove("records").and_then(|v| v.as_u64());
        let bytes = fields.remove("bytes").and_then(|v| v.as_u64());
        let dates_match = fields.remove("dates_match");
        if search_hit {
            fields.insert("conversation_id".into(), self.id.into());
        }
        values::fits(&row, self.budget)?;
        if row["neosian_format"] != 2 {
            if row["neosian_format"] == 1 && row["messages"].is_null() {
                let turn = codec::number(&row, "turn")?;
                row["messages"] = self.legacy_messages(turn).await?;
                if search_hit {
                    // The released history winner row includes the id and
                    // the original JSONB messages, before normalization.
                    values::fits(&row, self.budget)?;
                }
            }
            return Ok(Some(Stored::Whole(rows::turn(self.id, row)?)));
        }
        let turn = codec::number(&row, "turn")?;
        let (Some(records), Some(bytes)) = (records, bytes) else {
            return Err(damaged(
                self.id,
                turn,
                "its record mapping is missing or invalid",
            ));
        };
        if turn == 0
            || dates_match != Some(Value::Bool(true))
            || records == 0
            || row["redacted"] != false
            || !row["messages"].as_array().is_some_and(Vec::is_empty)
        {
            return Err(damaged(
                self.id,
                turn,
                "its record header has invalid fields",
            ));
        }
        Ok(Some(Stored::Records(Header {
            turn,
            created_at: codec::moment(&row, "created_at")?,
            actor: row["actor"].as_str().map(str::to_owned),
            redacted: false,
            records,
            bytes,
        })))
    }

    /// Bound the SQL interval itself so a generic query plan reads only the
    /// requested bodies. Stream the result to the visitor one row at a time.
    async fn interval(
        &self,
        turn: u64,
        first: u64,
        until: u64,
        mut visit: impl FnMut(TurnRecord) -> Result<bool, Error>,
    ) -> Result<bool, Error> {
        let params: [&(dyn tokio_postgres::types::ToSql + Sync); 4] =
            [&self.id, &(turn as i32), &signed(first), &signed(until)];
        let stream = self
            .tx
            .query_raw(&self.statements.range, params)
            .await
            .map_err(failure)?;
        futures_util::pin_mut!(stream);
        let mut seq = first;
        while let Some(row) = stream.try_next().await.map_err(failure)? {
            let value: Value = row.try_get("body").map_err(failure)?;
            values::fits(&value, self.budget)?;
            let record = decode_record(&row, seq, &value)
                .ok_or_else(|| damaged(self.id, turn, &format!("record {seq} is unreadable")))?;
            seq += 1;
            if !visit(record)? {
                return Ok(false);
            }
        }
        if seq != until {
            return Err(damaged(
                self.id,
                turn,
                "its records do not match its header",
            ));
        }
        Ok(true)
    }

    async fn records(
        &self,
        header: &Header,
        first: u64,
        mut visit: impl FnMut(TurnRecord) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        if first >= header.records {
            return Ok(());
        }
        let mut seq = first;
        while seq < header.records {
            let until = (seq + 8).min(header.records);
            if !self.interval(header.turn, seq, until, &mut visit).await? {
                return Ok(());
            }
            seq = until;
        }
        let overflow = self
            .tx
            .query_one(
                &self.statements.overflow,
                &[&self.id, &(header.turn as i32), &signed(header.records)],
            )
            .await
            .map_err(failure)?;
        if overflow.try_get::<_, bool>(0).map_err(failure)? {
            return Err(damaged(
                self.id,
                header.turn,
                "its records do not match its header",
            ));
        }
        Ok(())
    }

    async fn piece_start(&self, turn: u64, seq: u64) -> Result<u64, Error> {
        let row = self
            .tx
            .query_opt(
                &self.statements.piece,
                &[&self.id, &(turn as i32), &signed(seq)],
            )
            .await
            .map_err(failure)?;
        match row {
            Some(row) => {
                let from: i32 = row.try_get("first").map_err(failure)?;
                u64::try_from(from).map_err(|_| damaged(self.id, turn, "invalid first chunk"))
            }
            None => Ok(seq),
        }
    }

    async fn whole(&self, stored: Stored, maximum: u64) -> Result<Turn, Error> {
        let header = match stored {
            Stored::Records(header) => header,
            Stored::Whole(turn) => return Ok(turn),
        };
        if header.bytes > maximum {
            return Err(Error::Value(limits::turn_too_long(
                header.turn,
                self.id,
                header.bytes,
                maximum,
            )));
        }
        let turn = self.assemble(header).await?;
        self.check_whole(&turn, false)?;
        Ok(turn)
    }

    async fn assemble(&self, header: Header) -> Result<Turn, Error> {
        let bad = || damaged(self.id, header.turn, "its records do not match its header");
        let mut records = Vec::new();
        let mut bytes = 0_u64;
        self.records(&header, 0, |r| {
            bytes = bytes.checked_add(r.text.len() as u64).ok_or_else(bad)?;
            if bytes > header.bytes {
                return Err(bad());
            }
            records.push(r);
            Ok(true)
        })
        .await?;
        if bytes != header.bytes {
            return Err(bad());
        }
        let messages = messages_of(&records).map_err(|why| damaged(self.id, header.turn, &why))?;
        drop(records);
        Ok(Turn {
            conversation_id: self.id.into(),
            turn: header.turn,
            created_at: header.created_at,
            actor: header.actor,
            redacted: false,
            messages,
        })
    }

    // Charge the compact format-1 row returned by the same operation.
    // Search includes the id; ordinary read_turns already selected an id.
    fn check_whole(&self, turn: &Turn, search_hit: bool) -> Result<(), Error> {
        let mut empty = json!({"turn":turn.turn,"messages":[],"created_at":turn.created_at.z(),
            "neosian_format":1,"actor":turn.actor,"redacted":turn.redacted});
        if search_hit {
            empty["conversation_id"] = self.id.into();
        }
        let size = values::encoded(&empty)
            + turn.messages.len().saturating_sub(1) as u64
            + turn
                .messages
                .iter()
                .map(|m| values::encoded(&message::to_json(m)))
                .sum::<u64>();
        Ok(limits::check(limits::RESPONSE, size, self.budget)?)
    }
}

impl PostgresStore {
    pub(super) fn read_mixed_turns(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let maximum = limits::Records::load()?.max_turn_bytes;
        let budget = limits::response_bytes()?;
        self.pool.record_snapshot(async |tx, statements| {
            let db = Reader {
                tx,
                statements,
                id,
                budget,
            };
            let headers = db
                .headers((after as u64) + 1, u64::MAX, limit, false)
                .await?;
            while let Some(row) = db.next_header(&headers).await? {
                if !visit(db.whole(row, maximum).await?)? {
                    break;
                }
            }
            Ok(())
        })
    }
}
