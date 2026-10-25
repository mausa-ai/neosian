//! Consume existing wire-6 pages without collecting the complete listing.

use super::State;
use crate::store::{Entry, error::Error};
use sha2::Digest;
use std::collections::{BTreeSet, VecDeque};

pub const BATCH: i64 = 256;

pub struct Entries<'a> {
    store: &'a dyn State,
    scope: String,
    prefix: String,
    cursor: Option<String>,
    seen: BTreeSet<[u8; 32]>,
    rows: VecDeque<Entry>,
    done: bool,
    last: String,
}

impl<'a> Entries<'a> {
    pub fn new(store: &'a dyn State, scope: &str, prefix: &str) -> Self {
        Self {
            store,
            scope: scope.into(),
            prefix: prefix.into(),
            cursor: None,
            seen: BTreeSet::new(),
            rows: VecDeque::new(),
            done: false,
            last: String::new(),
        }
    }
    fn page(&mut self) -> Result<(), Error> {
        let page =
            self.store
                .document_page(&self.scope, &self.prefix, self.cursor.as_deref(), BATCH)?;
        if page.items.len() > BATCH as usize {
            return Err(Error::Value("backend exceeded document page size".into()));
        }
        let mut last = self.last.clone();
        for entry in &page.items {
            crate::store::grammar::validate_document_path(&entry.path)?;
            if entry.path <= last || !entry.path.starts_with(&self.prefix) {
                return Err(Error::Value(
                    "backend returned unordered or out-of-selection documents".into(),
                ));
            }
            last.clone_from(&entry.path);
        }
        self.last = last;
        if let Some(cursor) = &page.next_cursor
            && (self.seen.len() >= 100_000
                || cursor.is_empty()
                || cursor.len() > 8192
                || !self
                    .seen
                    .insert(sha2::Sha256::digest(cursor.as_bytes()).into()))
        {
            return Err(Error::Value(
                "backend returned an invalid or repeated document cursor".into(),
            ));
        }
        self.done = page.next_cursor.is_none();
        self.cursor = page.next_cursor;
        self.rows = page.items.into();
        Ok(())
    }
}

impl Iterator for Entries<'_> {
    type Item = Result<Entry, Error>;
    fn next(&mut self) -> Option<Self::Item> {
        loop {
            if let Some(row) = self.rows.pop_front() {
                return Some(Ok(row));
            }
            if self.done {
                return None;
            }
            if let Err(error) = self.page() {
                self.done = true;
                return Some(Err(error));
            }
        }
    }
}
