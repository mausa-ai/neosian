//! Paged listings beside the reads (`memory/pageable.py`,
//! `file_pageable.py`, DESIGN §8, #244): each page reads what the whole
//! read reads and keeps the rows after the cursor, so what a page bounds
//! is the answer, not the work. The keysets are the listings' own orders:
//! the path, the version; the ledger's are in `ledger.rs`.

use serde_json::json;

use super::Entry;
use super::FileStore;
use super::cursor::{self, Kind};
use super::error::Error;
use super::grammar::validate_document_path;
use super::journal::Row;

/// One page of a listing. `next_cursor` is None exactly when nothing
/// follows; otherwise it goes back with the same arguments.
pub struct Page<T> {
    pub items: Vec<T>,
    pub next_cursor: Option<String>,
}

/// `page_limit`: at least one row a page.
pub fn page_limit(limit: i64) -> Result<usize, Error> {
    if limit < 1 {
        return Err(Error::Value(format!(
            "a page limit must be >= 1, got {limit}"
        )));
    }
    Ok(limit as usize)
}

/// `paged`: a page cut from `limit + 1` fetched rows, the extra row only
/// saying that something follows, the cursor the last row kept.
pub fn paged<T>(mut items: Vec<T>, limit: usize, cursor_of: impl Fn(&T) -> String) -> Page<T> {
    if items.len() <= limit {
        return Page {
            items,
            next_cursor: None,
        };
    }
    items.truncate(limit);
    let next_cursor = items.last().map(cursor_of);
    Page { items, next_cursor }
}

impl FileStore {
    /// The live documents after `cursor`'s path, in path order.
    pub fn list_documents_page(
        &self,
        scope: &str,
        prefix: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Entry>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        let limit = page_limit(limit)?;
        let after = match cursor {
            Some(cursor) => Some(
                cursor::decode("list", cursor, &[Kind::Str]).map_err(Error::Value)?[0]
                    .as_str()
                    .expect("the codec checked the string")
                    .to_string(),
            ),
            None => None,
        };
        let mut entries = self.listing(scope, prefix)?;
        if let Some(after) = &after {
            entries.retain(|entry| entry.path > *after);
        }
        entries.truncate(limit + 1);
        Ok(paged(entries, limit, |entry| {
            cursor::encode("list", &[json!(entry.path)])
        }))
    }

    /// The version rows before `cursor`'s version, newest first.
    pub fn versions_page(
        &self,
        scope: &str,
        path: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Row>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        validate_document_path(path)?;
        let limit = page_limit(limit)?;
        let before = match cursor {
            Some(cursor) => Some(
                cursor::decode("versions", cursor, &[Kind::Int]).map_err(Error::Value)?[0]
                    .as_i64()
                    .expect("the codec checked the integer"),
            ),
            None => None,
        };
        let (_, mut rows) = self.rows(scope, path)?;
        rows.reverse();
        if let Some(before) = before {
            rows.retain(|row| (row.version as i64) < before);
        }
        rows.truncate(limit + 1);
        Ok(paged(rows, limit, |row| {
            cursor::encode("versions", &[json!(row.version)])
        }))
    }
}
