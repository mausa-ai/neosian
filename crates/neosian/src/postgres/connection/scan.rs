//! Streaming reads under one snapshot. Path-ordered memory search uses a portal
//! and stops at its lookahead; ranked search reads its facts statement as one
//! streamed statement and its winners with a second statement in the same
//! read-only REPEATABLE READ transaction (DESIGN §40.45), so both see one
//! snapshot and the operator's statement and transaction timeouts bound the
//! counting that happens while the rows stream. Turn reads that go to a spool
//! or a door's hits read one statement as a stream; only a size page, which
//! may stop early, reads through a portal in growing batches (§40.46, #417).
//! Statements are prepared once per connection. A read ends before its
//! answer is sent, so no connection or transaction waits on a client.

use super::{Connection, Pool, bind, failure, parameters, values};
use crate::conversation::page::Mode;
use crate::store::error::Error;
use serde_json::Value;
use std::io;
use tokio_postgres::{IsolationLevel, Statement, Transaction, types::ToSql};

/// A two-statement read: the first statement's rows, then (when `second` names
/// one) the second statement's rows, in one snapshot.
pub trait Passes {
    /// One row of the first statement; false stops reading it.
    fn first(&mut self, row: Value) -> Result<bool, Error>;
    /// The second statement and its parameters, decided after the first.
    fn second(&mut self) -> Result<Option<(&'static str, Value)>, Error>;
    /// One row of the second statement; false stops reading it.
    fn then(&mut self, row: Value) -> Result<bool, Error>;
}

/// The largest portal batch, so a page of many small rows still takes few
/// round trips and never asks for many more rows than it keeps.
const BATCH: i32 = 64;

/// A template's statement on this connection and its bound parameters.
async fn statement(
    connection: &mut Connection,
    schema: &str,
    template: &'static str,
    params: &Value,
) -> io::Result<(Statement, Vec<super::values::Parameter>)> {
    let (sql, names) = parameters(&template.replace("{{schema}}", schema));
    let statement = connection.prepared(template, &sql).await?;
    let bound = bind(&names, &statement, params)?;
    Ok((statement, bound))
}

impl Pool {
    pub fn scan(
        &self,
        template: &'static str,
        params: &Value,
        mut visit: impl FnMut(Value) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        self.runtime.block_on(async {
            let mut held = self.checkout().await?;
            let connection = held.as_mut().expect("connected");
            let (statement, bound) = statement(connection, &self.schema, template, params).await?;
            let tx = connection
                .client
                .build_transaction()
                .read_only(true)
                .start()
                .await
                .map_err(failure)?;
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

    /// Run `passes` in one read-only REPEATABLE READ transaction; the second
    /// statement reads through a portal only for a size page.
    pub fn snapshot(
        &self,
        template: &'static str,
        params: &Value,
        mode: Mode,
        passes: &mut impl Passes,
    ) -> Result<(), Error> {
        self.runtime.block_on(async {
            let mut held = self.checkout().await?;
            let connection = held.as_mut().expect("connected");
            let (facts, bound) = statement(connection, &self.schema, template, params).await?;
            let tx = connection
                .client
                .build_transaction()
                .isolation_level(IsolationLevel::RepeatableRead)
                .read_only(true)
                .start()
                .await
                .map_err(failure)?;
            stream(&tx, &facts, &bound, |row| passes.first(row)).await?;
            if let Some((template, params)) = passes.second()? {
                let (sql, names) = parameters(&template.replace("{{schema}}", &self.schema));
                let winners = tx.prepare(&sql).await.map_err(failure)?;
                let bound = bind(&names, &winners, &params)?;
                if let Mode::Paged(ceiling) = mode {
                    batches(&tx, &winners, &bound, ceiling, |row| passes.then(row)).await?;
                } else {
                    stream(&tx, &winners, &bound, |row| passes.then(row)).await?;
                }
            }
            tx.commit().await.map_err(failure)?;
            Ok(())
        })
    }

    /// One statement's rows handed to `visit` as they arrive, each charged
    /// against `NEOSIAN_MAX_RESPONSE_BYTES` as `mode` says (§40.46, #417).
    /// A size page reads through a portal in batches of 1, 2, 4 up to 64
    /// rows inside a read-only transaction, so false from `visit` stops
    /// before the next batch is asked for; every other read is one streamed
    /// statement and one round trip.
    pub fn rows(
        &self,
        template: &'static str,
        params: &Value,
        mode: Mode,
        mut visit: impl FnMut(Value) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let budget = crate::limits::response_bytes()?;
        self.runtime.block_on(async {
            let mut held = self.checkout().await?;
            let connection = held.as_mut().expect("connected");
            let (statement, bound) = statement(connection, &self.schema, template, params).await?;
            let mut used = 0_u64;
            let charged = |row: Value, used: &mut u64| {
                *used = mode.charge(*used, values::weight(&row));
                crate::limits::check(crate::limits::RESPONSE, *used, budget).map(|()| row)
            };
            let Mode::Paged(ceiling) = mode else {
                let rows = connection
                    .client
                    .query_raw(
                        &statement,
                        bound.iter().map(|v| &**v as &(dyn ToSql + Sync)),
                    )
                    .await
                    .map_err(failure)?;
                return each(rows, |row| visit(charged(row, &mut used)?)).await;
            };
            let tx = connection
                .client
                .build_transaction()
                .read_only(true)
                .start()
                .await
                .map_err(failure)?;
            let page = |row| visit(charged(row, &mut used)?);
            batches(&tx, &statement, &bound, ceiling, page).await?;
            tx.commit().await.map_err(failure)?;
            Ok(())
        })
    }
}

/// Every row of a running statement, decoded as it arrives; false stops.
async fn each(
    rows: tokio_postgres::RowStream,
    mut visit: impl FnMut(Value) -> Result<bool, Error>,
) -> Result<(), Error> {
    use futures_util::TryStreamExt;
    futures_util::pin_mut!(rows);
    while let Some(row) = rows.try_next().await.map_err(failure)? {
        if !visit(values::row(&row)?)? {
            break;
        }
    }
    Ok(())
}

/// One statement's rows inside the transaction, one at a time as the driver
/// receives them. The driver applies backpressure, so rows not yet visited
/// wait in the socket.
async fn stream(
    tx: &Transaction<'_>,
    statement: &Statement,
    bound: &[super::values::Parameter],
    visit: impl FnMut(Value) -> Result<bool, Error>,
) -> Result<(), Error> {
    let borrowed: Vec<&(dyn ToSql + Sync)> = bound.iter().map(|v| &**v as _).collect();
    let rows = tx
        .query_raw(statement, borrowed.iter().copied())
        .await
        .map_err(failure)?;
    each(rows, visit).await
}

/// One statement through a portal, each batch sized from the compact JSON
/// size of the rows already read to reach the page's `ceiling`, plus two
/// rows (1 row first, at most 64), so a page that stops asks for about two
/// rows more than it keeps; rows not yet asked for stay in the database.
async fn batches(
    tx: &Transaction<'_>,
    statement: &Statement,
    bound: &[super::values::Parameter],
    ceiling: u64,
    mut visit: impl FnMut(Value) -> Result<bool, Error>,
) -> Result<(), Error> {
    use futures_util::TryStreamExt;
    let borrowed: Vec<&(dyn ToSql + Sync)> = bound.iter().map(|v| &**v as _).collect();
    let portal = tx.bind(statement, &borrowed).await.map_err(failure)?;
    let (mut size, mut read, mut weight) = (1_i32, 0_u64, 0_u64);
    loop {
        let rows = tx.query_portal_raw(&portal, size).await.map_err(failure)?;
        futures_util::pin_mut!(rows);
        let mut count = 0;
        while let Some(row) = rows.try_next().await.map_err(failure)? {
            count += 1;
            let row = values::row(&row)?;
            read += 1;
            weight = weight.saturating_add(values::encoded(&row));
            if !visit(row)? {
                return Ok(());
            }
        }
        if count < size {
            return Ok(());
        }
        let average = (weight / read).max(1);
        let wanted = ceiling.saturating_sub(weight) / average + 2;
        size = i32::try_from(wanted).unwrap_or(BATCH).clamp(1, BATCH);
    }
}
