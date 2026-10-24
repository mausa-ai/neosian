//! The two ledger reads and their pages (`memory/file.py`'s `history` and
//! `redactions`, `file_pageable.py`, DESIGN §8, §20): every version row of
//! a scope across every path ever written, deleted paths included, newest
//! first; the erasure trail read backwards. The whole reads land at R2
//! with `serve`, their first caller in the binary (the carry to R4 struck).

use serde_json::json;

use super::FileStore;
use super::cursor::{self, Kind, instant};
use super::error::Error;
use super::grammar::parse_scope;
use super::journal::{self, Act, Row};
use super::layout::{self, REDACTIONS, VERSIONS};
use super::pages::{Page, page_limit, paged};
use super::time::Stamp;

/// A keyset into the ledger's order: `created_at` desc, path, version desc.
pub struct HistoryKey {
    pub created_at: Stamp,
    pub path: String,
    pub version: i64,
}

impl HistoryKey {
    /// `history_key`: the parts of a `history` cursor.
    pub fn decode(cursor: &str) -> Result<Self, Error> {
        let parts = cursor::decode("history", cursor, &[Kind::Instant, Kind::Str, Kind::Int])
            .map_err(Error::Value)?;
        let text = |at: usize| parts[at].as_str().unwrap_or_default();
        Ok(Self {
            created_at: Stamp::parse(text(0)).expect("the codec checked the instant"),
            path: text(1).to_string(),
            version: parts[2].as_i64().expect("the codec checked the integer"),
        })
    }

    /// Whether `row` comes after the keyset in the ledger's order.
    fn precedes(&self, row: &Row) -> bool {
        if row.created_at.instant() != self.created_at.instant() {
            return row.created_at.instant() < self.created_at.instant();
        }
        if row.path != self.path {
            return row.path > self.path;
        }
        i128::from(row.version) < i128::from(self.version)
    }
}

/// `history_cursor`: the row's own position.
fn history_cursor(row: &Row) -> String {
    cursor::encode(
        "history",
        &[
            json!(instant(row.created_at)),
            json!(row.path),
            json!(row.version),
        ],
    )
}

/// `newest_first`: `created_at` desc, then path, then version desc.
fn newest_first(rows: &mut [Row]) {
    rows.sort_by(|a, b| {
        (b.created_at.instant(), &a.path, b.version).cmp(&(
            a.created_at.instant(),
            &b.path,
            a.version,
        ))
    });
}

fn after(stamp: Stamp, since: Option<Stamp>) -> bool {
    since.is_none_or(|since| stamp.instant() >= since.instant())
}

impl FileStore {
    /// Every version row of the scope, newest first, `since` inclusive.
    pub fn history(&self, scope: &str, since: Option<Stamp>) -> Result<Vec<Row>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        self.ledger(scope, since)
    }

    fn ledger(&self, scope: &str, since: Option<Stamp>) -> Result<Vec<Row>, Error> {
        let versions = layout::scope_dir(&self.root, scope).join(VERSIONS);
        let mut rows = Vec::new();
        for path in layout::logical_paths(&versions, ".jsonl")? {
            let (_, read) = self.rows(scope, &path)?;
            rows.extend(read.into_iter().filter(|row| after(row.created_at, since)));
        }
        newest_first(&mut rows);
        Ok(rows)
    }

    /// The erasure trail, newest first, `since` inclusive.
    pub fn redactions(&self, scope: &str, since: Option<Stamp>) -> Result<Vec<Act>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        let mut acts = self.trail(scope)?;
        acts.reverse();
        acts.retain(|act| after(act.created_at, since));
        Ok(acts)
    }

    fn trail(&self, scope: &str) -> Result<Vec<Act>, Error> {
        let trail = layout::scope_dir(&self.root, scope).join(REDACTIONS);
        Ok(journal::read_acts(&trail, scope)?)
    }

    /// One page of the ledger: the rows after the cursor's keyset.
    pub fn history_page(
        &self,
        scope: &str,
        since: Option<Stamp>,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Row>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        let limit = page_limit(limit)?;
        let key = cursor.map(HistoryKey::decode).transpose()?;
        let mut rows = self.ledger(scope, since)?;
        if let Some(key) = &key {
            rows.retain(|row| key.precedes(row));
        }
        rows.truncate(limit + 1);
        Ok(paged(rows, limit, history_cursor))
    }

    /// One page of the trail: newest first is the trail read backwards,
    /// and a line never moves, so the keyset is the line.
    pub fn redactions_page(
        &self,
        scope: &str,
        since: Option<Stamp>,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Act>, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        let limit = page_limit(limit)?;
        let before = match cursor {
            Some(cursor) => Some(
                cursor::decode("trail", cursor, &[Kind::Int]).map_err(Error::Value)?[0]
                    .as_i64()
                    .expect("the codec checked the integer"),
            ),
            None => None,
        };
        let acts = self.trail(scope)?;
        let lines: Vec<(usize, Act)> = acts
            .into_iter()
            .enumerate()
            .rev()
            .filter(|(line, act)| {
                before.is_none_or(|before| (*line as i64) < before) && after(act.created_at, since)
            })
            .take(limit + 1)
            .collect();
        let page = paged(lines, limit, |(line, _)| {
            cursor::encode("trail", &[json!(line)])
        });
        Ok(Page {
            items: page.items.into_iter().map(|(_, act)| act).collect(),
            next_cursor: page.next_cursor,
        })
    }
}

/// The scope grammar alone, for a restore (which checks no case fold).
pub fn check_scope(scope: &str) -> Result<(), Error> {
    parse_scope(scope)?;
    Ok(())
}
