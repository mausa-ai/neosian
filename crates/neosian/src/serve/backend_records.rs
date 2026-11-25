//! Turn records behind the server (DESIGN §40.54 items 8 to 13): what a
//! backend answers for the routes of `routes_records`, beside the callers
//! of `backend`. Files read the records marker on every request, so a
//! store enabled while the server runs is served without a restart;
//! Postgres answers from the layout it detected once.

use crate::conversation::Error;
use crate::conversation::turn_records::{Write, Written};
use crate::limits;
use crate::postgres::PostgresStore;
use crate::store::FileStore;
use crate::store::error::MemoryError;

pub trait Records {
    /// Whether this store keeps turns as records.
    fn supports_records(&self) -> Result<bool, Error>;
    /// The store's own refusal of a records route when it keeps none.
    fn records_absent(&self) -> MemoryError;
    fn write_records(
        &self,
        id: &str,
        write: Write,
        limits: &limits::Records,
    ) -> Result<Written, Error>;
}

impl Records for FileStore {
    fn supports_records(&self) -> Result<bool, Error> {
        self.records_enabled()
    }
    fn records_absent(&self) -> MemoryError {
        Self::records_absent()
    }
    fn write_records(
        &self,
        id: &str,
        write: Write,
        limits: &limits::Records,
    ) -> Result<Written, Error> {
        self.write_records(id, write, limits)
    }
}

impl Records for PostgresStore {
    fn supports_records(&self) -> Result<bool, Error> {
        Ok(self.supports_records()?)
    }
    fn records_absent(&self) -> MemoryError {
        Self::records_absent()
    }
    fn write_records(
        &self,
        id: &str,
        write: Write,
        limits: &limits::Records,
    ) -> Result<Written, Error> {
        self.write_records(id, write, limits)
    }
}
