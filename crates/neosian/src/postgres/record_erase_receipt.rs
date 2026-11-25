//! Erase folded seal headers and digest pages in the turn redaction transaction.
use super::connection::{Failure, bind, parameters};
use super::{PostgresStore, sql, turns, values};
use crate::conversation::{Error, Refused};
use serde_json::Value;
use tokio_postgres::types::ToSql;
use tokio_postgres::{Row, Statement, Transaction};

async fn bound(
    tx: &Transaction<'_>,
    template: &str,
    params: &Value,
) -> Result<(Statement, Vec<values::Parameter>), Failure> {
    let (sql, names) = parameters(template);
    let stmt = tx.prepare(&sql).await?;
    let bound = bind(&names, &stmt, params).map_err(Error::from)?;
    Ok((stmt, bound))
}

async fn one(tx: &Transaction<'_>, template: &str, params: &Value) -> Result<Row, Failure> {
    let (stmt, bound) = bound(tx, template, params).await?;
    let args: Vec<&(dyn ToSql + Sync)> = bound.iter().map(|p| &**p as _).collect();
    Ok(tx.query_one(&stmt, &args).await?)
}

impl PostgresStore {
    pub(super) fn erase_with_receipts(&self, id: &str, params: &Value) -> Result<usize, Error> {
        let schema = self.pool.schema();
        let mutator = sql!("records/redact_turns").replace("{{schema}}", schema);
        let scope = sql!("records/erase_seal_scope").replace("{{schema}}", schema);
        let targets = ["record_seal_pages", "record_seals"]
            .map(|name| format!("{schema}.{name} r WHERE {scope}"));
        let budget = crate::limits::response_bytes()?;
        self.pool.record_mutation(id, async |tx| {
            let row = one(tx, &mutator, params).await?;
            let value = values::row(&row).map_err(Error::from)?;
            values::fits(&value, budget).map_err(Error::from)?;
            let n = turns::erased(id, &value)?;
            // Full erasure can encounter detached pages without a surviving
            // owning header or visible turn. Explicitly delete page rows first.
            for target in &targets {
                let delete = format!("DELETE FROM {target}");
                let (statement, bound) = bound(tx, &delete, params).await?;
                let args = bound.iter().map(|p| &**p as &(dyn ToSql + Sync));
                tx.execute_raw(&statement, args).await?;
            }
            // A BEFORE DELETE trigger can suppress even an FK cascade.
            // A subsequent statement sees our own writes. Any survivor aborts
            // the redactions and trail entry together with the receipt deletes.
            for target in &targets {
                let check = format!("SELECT EXISTS (SELECT FROM {target})");
                if one(tx, &check, params).await?.try_get::<_, bool>(0)? {
                    let why = "could not erase all selected folded seal receipts";
                    return Err(Refused::format_unsupported(id, why).into());
                }
            }
            Ok(n)
        })
    }
}
