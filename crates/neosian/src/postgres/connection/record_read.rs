//! Bind a header portal and its record-range reads to a single MVCC snapshot.
use super::{Connection, Pool, failure};
use crate::conversation::Error;
use crate::postgres::{record_read::Statements, sql};
use tokio_postgres::{IsolationLevel, Statement, Transaction};

async fn prepare(
    conn: &mut Connection,
    schema: &str,
    template: &'static str,
) -> Result<Statement, Error> {
    Ok(conn
        .prepared(template, &template.replace("{{schema}}", schema))
        .await?)
}

impl Pool {
    pub(in crate::postgres) fn record_snapshot<T>(
        &self,
        read: impl AsyncFnOnce(&Transaction<'_>, &Statements) -> Result<T, Error>,
    ) -> Result<T, Error> {
        self.runtime.block_on(async {
            let mut held = self.checkout().await?;
            let conn = held.as_mut().expect("connected");
            let statements = Statements {
                headers: prepare(conn, &self.schema, sql!("records/read_headers")).await?,
                legacy: prepare(conn, &self.schema, sql!("records/read_legacy")).await?,
                head_scan: prepare(conn, &self.schema, sql!("records/read_head_scan")).await?,
                overflow: prepare(conn, &self.schema, sql!("records/read_overflow")).await?,
                range: prepare(conn, &self.schema, sql!("records/read_range")).await?,
                piece: prepare(conn, &self.schema, sql!("records/read_piece")).await?,
            };
            let tx = conn
                .client
                .build_transaction()
                .isolation_level(IsolationLevel::RepeatableRead)
                .read_only(true)
                .start()
                .await
                .map_err(failure)?;
            let result = read(&tx, &statements).await?;
            tx.commit().await.map_err(failure)?;
            Ok(result)
        })
    }
}
