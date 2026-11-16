//! Streaming reads under one snapshot. Path-ordered memory search uses a portal
//! and stops at its lookahead; ranked search reads its facts statement as one
//! streamed statement and its winners with a second statement in the same
//! read-only REPEATABLE READ transaction (DESIGN §40.45), so both see one
//! snapshot and the operator's statement and transaction timeouts bound the
//! counting that happens while the rows stream.

use super::{CONNECTIONS, Pool, bind, failure, parameters, values};
use crate::store::error::Error;
use serde_json::Value;
use std::{io, sync::atomic::Ordering, time::Duration};
use tokio_postgres::{IsolationLevel, Transaction, types::ToSql};

/// A two-statement read: the first statement's rows, then (when `second` names
/// one) the second statement's rows, in one snapshot.
pub trait Passes {
    /// One row of the first statement; false stops reading it.
    fn first(&mut self, row: Value) -> Result<bool, Error>;
    /// The second statement and its parameters, decided after the first.
    fn second(&mut self) -> Result<Option<(&'static str, Value)>, Error>;
    /// One row of the second statement.
    fn then(&mut self, row: Value) -> Result<(), Error>;
}

impl Pool {
    pub fn scan(
        &self,
        template: &'static str,
        params: &Value,
        mut visit: impl FnMut(Value) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let (sql, names) = parameters(&template.replace("{{schema}}", &self.schema));
        self.runtime.block_on(async {
            let slot = self.next.fetch_add(1, Ordering::Relaxed) % CONNECTIONS;
            let mut held = tokio::time::timeout(Duration::from_secs(30), self.slots[slot].lock())
                .await
                .map_err(|_| io::Error::other("Postgres connection checkout timed out"))?;
            if held.as_ref().is_none_or(|c| c.client.is_closed()) {
                *held = Some(self.connect().await.map_err(failure)?);
            }
            let connection = held.as_mut().expect("connected");
            let tx = connection
                .client
                .build_transaction()
                .read_only(true)
                .start()
                .await
                .map_err(failure)?;
            let statement = tx.prepare(&sql).await.map_err(failure)?;
            let bound = bind(&names, &statement, params)?;
            let borrowed: Vec<&(dyn ToSql + Sync)> = bound.iter().map(|v| &**v as _).collect();
            let portal = tx.bind(&statement, &borrowed).await.map_err(failure)?;
            loop {
                // A candidate can be much larger than an excerpt. Fetching one
                // row prevents collection size from multiplying body allocation.
                let rows = tx.query_portal(&portal, 1).await.map_err(failure)?;
                let Some(row) = rows.first() else {
                    break;
                };
                if !visit(values::row(row)?)? {
                    break;
                }
            }
            tx.commit().await.map_err(failure)?;
            Ok(())
        })
    }

    /// Run `passes` in one read-only REPEATABLE READ transaction.
    pub fn snapshot(
        &self,
        template: &'static str,
        params: &Value,
        passes: &mut impl Passes,
    ) -> Result<(), Error> {
        self.runtime.block_on(async {
            let slot = self.next.fetch_add(1, Ordering::Relaxed) % CONNECTIONS;
            let mut held = tokio::time::timeout(Duration::from_secs(30), self.slots[slot].lock())
                .await
                .map_err(|_| io::Error::other("Postgres connection checkout timed out"))?;
            if held.as_ref().is_none_or(|c| c.client.is_closed()) {
                *held = Some(self.connect().await.map_err(failure)?);
            }
            let connection = held.as_mut().expect("connected");
            let tx = connection
                .client
                .build_transaction()
                .isolation_level(IsolationLevel::RepeatableRead)
                .read_only(true)
                .start()
                .await
                .map_err(failure)?;
            stream(&tx, &self.schema, template, params, |row| passes.first(row)).await?;
            if let Some((template, params)) = passes.second()? {
                stream(&tx, &self.schema, template, &params, |row| {
                    passes.then(row)?;
                    Ok(true)
                })
                .await?;
            }
            tx.commit().await.map_err(failure)?;
            Ok(())
        })
    }
}

/// One statement's rows, one at a time as the driver receives them. The driver
/// applies backpressure, so rows not yet visited wait in the socket.
async fn stream(
    tx: &Transaction<'_>,
    schema: &str,
    template: &'static str,
    params: &Value,
    mut visit: impl FnMut(Value) -> Result<bool, Error>,
) -> Result<(), Error> {
    use futures_util::TryStreamExt;
    let (sql, names) = parameters(&template.replace("{{schema}}", schema));
    let statement = tx.prepare(&sql).await.map_err(failure)?;
    let bound = bind(&names, &statement, params)?;
    let borrowed: Vec<&(dyn ToSql + Sync)> = bound.iter().map(|v| &**v as _).collect();
    let rows = tx
        .query_raw(&statement, borrowed.iter().copied())
        .await
        .map_err(failure)?;
    futures_util::pin_mut!(rows);
    while let Some(row) = rows.try_next().await.map_err(failure)? {
        if !visit(values::row(&row)?)? {
            break;
        }
    }
    Ok(())
}
