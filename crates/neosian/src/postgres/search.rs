//! A statement snapshot feeds the same matcher as FileStore, without schema changes.

use super::{PostgresStore, rows, sql};
use crate::memory::search::{self, Page, Request};
use crate::store::error::Error;
use serde_json::json;

impl PostgresStore {
    pub fn search_memory(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        let after = request.scope_after(scope)?;
        let maximum = crate::limits::file_bytes()?;
        let mut scan = search::Scan::new(scope, request)?;
        self.pool.scan(sql!("store/search_memory"), &json!({
            "scope": scope, "pattern": format!("{}%", rows::escaped(&request.prefix)), "after": after,
            "bytes": maximum
        }), |row| {
            crate::limits::check(crate::limits::FILE, crate::remote::codec::number(&row, "record_bytes")?, maximum)?;
            let path = crate::remote::codec::text(&row, "path")?;
            rows::address(scope, &path)?;
            let doc = rows::document(scope, &path, &row)?;
            scan.push(path, doc)
        })?;
        Ok(scan.finish())
    }
}
