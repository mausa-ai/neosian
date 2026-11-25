//! Detect the optional operator-installed records extension on first use.

use super::{PostgresStore, sql};
use crate::store::error::{Error, MemoryError};

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Layout {
    Plain,
    Records,
    FoldReceipts,
}

impl PostgresStore {
    /// What a records operation answers on a schema without the extension.
    pub(crate) fn records_absent() -> MemoryError {
        MemoryError::configuration(
            "records are not enabled on this store; install Postgres records extension 1",
        )
    }

    pub(crate) fn require_records(&self) -> Result<(), Error> {
        if !self.supports_records()? {
            return Err(Self::records_absent().into());
        }
        Ok(())
    }

    /// Exact stored names for the operator's explicit migration selection.
    /// A pending write establishes a conversation before its first seal.
    pub(crate) fn stored_migration_names(&self, named: &[String]) -> Result<Vec<String>, Error> {
        self.operator_names(sql!("records/migration_names"), named)
    }

    pub(crate) fn fold_names(&self, named: &[String]) -> Result<Vec<String>, Error> {
        self.operator_names(sql!("records/fold_names"), named)
    }

    fn operator_names(&self, query: &'static str, named: &[String]) -> Result<Vec<String>, Error> {
        self.fetch(query, serde_json::json!({"named":named}))?
            .iter()
            .map(|row| crate::remote::codec::text(row, "conversation_id"))
            .collect()
    }

    pub fn supports_records(&self) -> Result<bool, Error> {
        Ok(self.records_layout()? != Layout::Plain)
    }

    pub(super) fn supports_fold_receipts(&self) -> Result<bool, Error> {
        Ok(self.records_layout()? == Layout::FoldReceipts)
    }

    /// Operators pause and restart writers when installing or upgrading. Cache
    /// a successful catalog observation, including a plain reference schema.
    fn records_layout(&self) -> Result<Layout, Error> {
        if let Some(&layout) = self.records.get() {
            return Ok(layout);
        }
        let incomplete = || MemoryError::configuration("incomplete Postgres records extension");
        let base = self.pool.catalog_flags(sql!("records/detect"))?;
        let version = match base {
            0 => 0,
            2 => match self.pool.catalog_flags(sql!("records/version"))? {
                0 => return Err(incomplete().into()),
                v @ (1 | 2) => v,
                _ => {
                    return Err(MemoryError::configuration(
                        "unsupported Postgres records extension",
                    )
                    .into());
                }
            },
            _ => return Err(incomplete().into()),
        };
        let seals = self.pool.catalog_flags(sql!("records/seal_layout"))?;
        let layout = match (version, seals) {
            (0, 0) => Layout::Plain,
            (1, 0) => Layout::Records,
            (2, 2) => Layout::FoldReceipts,
            _ => return Err(incomplete().into()),
        };
        Ok(*self.records.get_or_init(|| layout))
    }
}
