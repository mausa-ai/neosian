//! Stream and verify the exact mapped part within a read snapshot or writer transaction.
use super::{
    Error, Header, Turn, TurnRecord, Value, damaged, decode_record, failure, signed, values,
};
use crate::conversation::turn_records::messages_of;
use futures_util::TryStreamExt;
use tokio_postgres::{Statement, Transaction};

pub(in crate::postgres) struct Bodies<'a, 'c> {
    pub(in crate::postgres) tx: &'a Transaction<'c>,
    pub(in crate::postgres) range: &'a Statement,
    pub(in crate::postgres) overflow: &'a Statement,
    pub(in crate::postgres) id: &'a str,
    pub(in crate::postgres) budget: u64,
}

impl Bodies<'_, '_> {
    /// Bound the SQL interval itself so a generic query plan reads only the
    /// requested bodies. Stream the result to the visitor one row at a time.
    pub(super) async fn interval(
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
            .query_raw(self.range, params)
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

    pub(super) async fn records(
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
                self.overflow,
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

    pub(in crate::postgres) async fn assemble(&self, header: Header) -> Result<Turn, Error> {
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
}
