//! One portal retains the query snapshot while the matcher consumes one row at a time.

use super::{CONNECTIONS, Pool, failure, parameters, values};
use crate::store::error::Error;
use serde_json::Value;
use std::{io, sync::atomic::Ordering, time::Duration};
use tokio_postgres::types::ToSql;

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
            let bound = names
                .iter()
                .zip(statement.params())
                .map(|(name, ty)| {
                    values::parameter(
                        params
                            .get(name)
                            .ok_or_else(|| io::Error::other("missing SQL parameter"))?,
                        ty,
                    )
                })
                .collect::<io::Result<Vec<_>>>()?;
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
}
