//! Detect the optional operator-installed records extension on first use.

use super::{PostgresStore, sql};
use crate::store::error::{Error, MemoryError};
use serde_json::json;

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
        let found = self.one(sql!("records/detect"), json!({}), false)?;
        let supported = match (found["present"].as_u64(), found["tables"].as_u64()) {
            (Some(0), Some(0)) => false,
            (Some(5), Some(5)) => {
                let rows = self.fetch(sql!("records/version"), json!({}))?;
                let [row] = rows.as_slice() else {
                    return Err(incomplete().into());
                };
                let marker = &row["marker"];
                if marker["singleton"] != true {
                    return Err(incomplete().into());
                }
                match marker["version"].as_i64() {
                    Some(1) => true,
                    Some(_) => {
                        return Err(MemoryError::configuration(
                            "unsupported Postgres records extension",
                        )
                        .into());
                    }
                    None => return Err(incomplete().into()),
                }
            }
            _ => return Err(incomplete().into()),
        };
        Ok(*self.records.get_or_init(|| supported))
    }
}
