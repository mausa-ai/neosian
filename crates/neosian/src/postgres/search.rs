//! A statement snapshot feeds the same matcher as FileStore, without schema changes.
//! Path order streams through a portal to its lookahead; relevance and newest
//! read their facts and then their winners in one snapshot (DESIGN §40.45).

use super::connection::Passes;
use super::{PostgresStore, rows, sql};
use crate::memory::search::{self, Page, Request, Scan};
use crate::remote::codec;
use crate::store::{Document, error::Error};
use serde_json::{Value, json};

impl PostgresStore {
    pub fn search_memory(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        let after = request.scope_after(scope)?;
        let maximum = crate::limits::file_bytes()?;
        let mut passes = Ranked {
            scope,
            maximum,
            scan: search::Scan::new(scope, request)?,
        };
        let params = json!({
            "scope": scope, "pattern": format!("{}%", rows::escaped(&request.prefix)), "after": after,
            "bytes": maximum
        });
        if passes.scan.ranked() {
            self.pool
                .snapshot(sql!("store/search_memory"), &params, &mut passes)?;
        } else {
            self.pool
                .scan(sql!("store/search_memory"), &params, |row| {
                    passes.first(row)
                })?;
        }
        passes.scan.finish()
    }
}

struct Ranked<'a> {
    scope: &'a str,
    maximum: u64,
    scan: Scan<'a>,
}

impl Ranked<'_> {
    fn document(&self, row: &Value) -> Result<(String, Document), Error> {
        crate::limits::check(
            crate::limits::FILE,
            codec::number(row, "record_bytes")?,
            self.maximum,
        )?;
        let path = codec::text(row, "path")?;
        rows::address(self.scope, &path)?;
        let document = rows::document(self.scope, &path, row)?;
        Ok((path, document))
    }
}

impl Passes for Ranked<'_> {
    fn first(&mut self, row: Value) -> Result<bool, Error> {
        let (path, document) = self.document(&row)?;
        self.scan.push(path, document)
    }

    fn second(&mut self) -> Result<Option<(&'static str, Value)>, Error> {
        let paths = self.scan.winners();
        if paths.is_empty() {
            return Ok(None);
        }
        Ok(Some((
            sql!("store/search_memory_winners"),
            json!({"scope": self.scope, "paths": paths, "bytes": self.maximum}),
        )))
    }

    fn then(&mut self, row: Value) -> Result<bool, Error> {
        let (path, document) = self.document(&row)?;
        self.scan.build(path, document)?;
        Ok(true)
    }
}
