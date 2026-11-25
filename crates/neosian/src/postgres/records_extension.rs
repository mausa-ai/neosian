//! Detect the optional operator-installed records extension on first use.

use super::{PostgresStore, sql};
use crate::store::error::{Error, MemoryError};

impl PostgresStore {
    pub(super) fn require_records(&self) -> Result<(), Error> {
        if !self.supports_records()? {
            return Err(MemoryError::configuration(
                "records are not enabled on this store; install Postgres records extension 1",
            )
            .into());
        }
        Ok(())
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
