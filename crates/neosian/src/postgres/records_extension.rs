//! Detect the optional operator-installed records extension on first use.

use super::{PostgresStore, sql};
use crate::store::error::{Error, MemoryError};

impl PostgresStore {
    pub(crate) fn require_records(&self) -> Result<(), Error> {
        if !self.supports_records()? {
            return Err(MemoryError::configuration(
                "records are not enabled on this store; install Postgres records extension 1",
            )
            .into());
        }
        Ok(())
    }

    /// Exact stored names for the operator's explicit migration selection.
    /// A pending write establishes a conversation before its first seal.
    pub(crate) fn stored_migration_names(&self, named: &[String]) -> Result<Vec<String>, Error> {
        self.fetch(
            sql!("records/migration_names"),
            serde_json::json!({"named":named}),
        )?
        .iter()
        .map(|row| crate::remote::codec::text(row, "conversation_id"))
        .collect()
    }

    /// Enabling the extension requires restarting writers. A store therefore
    /// keeps its first successful check, including the plain schema-4 case.
    pub fn supports_records(&self) -> Result<bool, Error> {
        if let Some(&supported) = self.records.get() {
            return Ok(supported);
        }
        let incomplete = || MemoryError::configuration("incomplete Postgres records extension");
        let supported = match self.pool.catalog_flags(sql!("records/detect"))? {
            0 => false,
            2 => match self.pool.catalog_flags(sql!("records/version"))? {
                1 => true,
                2 => {
                    return Err(MemoryError::configuration(
                        "unsupported Postgres records extension",
                    )
                    .into());
                }
                _ => return Err(incomplete().into()),
            },
            _ => return Err(incomplete().into()),
        };
        Ok(*self.records.get_or_init(|| supported))
    }
}
