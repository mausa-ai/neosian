//! A statement snapshot feeds the same matcher as FileStore, without schema changes.

use super::{PostgresStore, rows, sql};
use crate::memory::search::{self, Page, Request};
use crate::store::error::Error;
use serde_json::json;

impl PostgresStore {
    pub fn search_memory(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        let after = request.scope_after(scope)?;
        let documents = self.fetch(sql!("store/search_memory"), json!({
            "scope": scope, "pattern": format!("{}%", rows::escaped(&request.prefix)), "after": after
        }))?;
        let selected = documents.iter().map(|row| {
            let path = crate::remote::codec::text(row, "path")?;
            rows::address(scope, &path)?;
            let doc = rows::document(scope, &path, row)?;
            Ok((path, doc))
        });
        search::scan(scope, request, selected)
    }
}
