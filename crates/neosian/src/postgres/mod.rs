//! Postgres over the same atomic statements as the Python reference store.
//! The schema is operator-owned; opening a store never applies DDL.

mod connection;
mod memory;
mod pages;
mod portable;
mod rows;
mod state;
mod tls;
mod turns;
mod values;

use crate::repr::repr;
use crate::store::error::{Error, MemoryError};
use serde_json::Value;

macro_rules! sql {
    ($name:literal) => {
        include_str!(concat!(env!("NEOSIAN_ASSETS"), "/sql/", $name, ".sql"))
    };
}
pub(crate) use sql;

pub struct PostgresStore {
    pool: connection::Pool,
}

pub fn validate_schema(name: &str) -> Result<(), MemoryError> {
    let bytes = name.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 63
        || !(bytes[0].is_ascii_lowercase() || bytes[0] == b'_')
        || !bytes
            .iter()
            .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || *ch == b'_')
    {
        return Err(MemoryError::configuration(format!(
            "Invalid Postgres schema name {}: must match [a-z_][a-z0-9_]{{0,62}}",
            repr(name)
        )));
    }
    Ok(())
}

impl PostgresStore {
    pub fn open(dsn: &str, schema: &str) -> Result<Self, Error> {
        validate_schema(schema)?;
        Ok(Self {
            pool: connection::Pool::new(dsn, schema)?,
        })
    }

    fn fetch(&self, sql: &'static str, params: Value) -> Result<Vec<Value>, Error> {
        Ok(self.pool.fetch(sql, &params, false)?)
    }

    fn one(&self, sql: &'static str, params: Value, retry: bool) -> Result<Value, Error> {
        let mut rows = self.pool.fetch(sql, &params, retry)?;
        if rows.len() != 1 {
            return Err(std::io::Error::other("expected one Postgres diagnostics row").into());
        }
        Ok(rows.remove(0))
    }
}
