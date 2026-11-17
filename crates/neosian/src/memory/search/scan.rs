//! Incremental matching under the backend's stable read boundary.
//!
//! Path order streams in path order and stops after its lookahead. Relevance
//! and newest read the whole eligible selection keeping facts, not hits:
//! relevance a path, counts and length per candidate, newest at most
//! `limit` keys. The backend then reads the winners again, in the same
//! boundary, and only they get excerpts and passages (DESIGN §40.45).

use std::collections::HashMap;

use super::rank::{Best, Facts, Stats};
use super::{Excerpt, Hit, Order, Page, Passage, Request, matching, searchable};
use crate::store::{Document, error::Error, time::Stamp};
use serde_json::json;

struct NewestKey {
    path: Box<str>,
    updated: i64,
}

/// Store all relevance paths without one allocation per document or a
/// growing buffer that would have to copy all earlier paths.
#[derive(Default)]
struct Paths {
    blocks: Vec<String>,
}

struct PathRef {
    start: usize,
    len: usize,
}

impl Paths {
    const BLOCK_BYTES: usize = 64 * 1024;

    fn keep(&mut self, path: &str) -> PathRef {
        if self
            .blocks
            .last()
            .is_none_or(|block| block.len() + path.len() > Self::BLOCK_BYTES)
        {
            self.blocks.push(String::with_capacity(Self::BLOCK_BYTES));
        }
        let index = self.blocks.len() - 1;
        let block = &mut self.blocks[index];
        let start = index * Self::BLOCK_BYTES + block.len();
        block.push_str(path);
        PathRef {
            start,
            len: path.len(),
        }
    }

    fn get(&self, key: &PathRef) -> &str {
        let block = &self.blocks[key.start / Self::BLOCK_BYTES];
        let offset = key.start % Self::BLOCK_BYTES;
        &block[offset..offset + key.len]
    }
}

pub struct Scan<'a> {
    scope: &'a str,
    request: &'a Request,
    order: Order,
    after: String,
    terms: Vec<String>,
    now: Stamp,
    bounds: (Option<i64>, Option<i64>),
    page: Page,
    stats: Stats,
    paths: Paths,
    facts: Facts<PathRef>,
    newest: Best<NewestKey>,
    winners: Vec<(String, Option<u64>)>,
    built: HashMap<String, Hit>,
}

impl<'a> Scan<'a> {
    pub fn new(scope: &'a str, request: &'a Request) -> Result<Self, Error> {
        let terms = matching::terms(&request.query);
        let order = request.order.unwrap_or(Order::Path);
        // Only relevance keeps a fact per candidate, so only it reads the bound.
        let maximum = if order == Order::Relevance {
            crate::limits::search_candidates()?
        } else {
            0
        };
        Ok(Self {
            scope,
            request,
            order,
            after: request.scope_after(scope)?,
            bounds: request.bounds().map_err(Error::Value)?,
            stats: Stats::new(terms.len()),
            paths: Paths::default(),
            facts: Facts::new(terms.len(), maximum),
            newest: Best::new(request.limit),
            terms,
            now: Stamp::now(),
            page: Page::empty(request.order),
            winners: Vec::new(),
            built: HashMap::new(),
        })
    }

    /// Whether this scan needs a second read of its winners.
    pub fn ranked(&self) -> bool {
        self.order.ranked()
    }

    fn eligible(&self, path: &str, document: &Document) -> bool {
        let request = self.request;
        let updated = document.envelope.updated_at.instant();
        path > self.after.as_str()
            && path.starts_with(&request.prefix)
            && searchable(path)
            && !document.envelope.redacted
            && self.bounds.0.is_none_or(|since| updated >= since)
            && self.bounds.1.is_none_or(|before| updated < before)
            && crate::knowledge::governance::eligible(
                request.knowledge_policy,
                self.scope,
                path,
                &document.content,
                self.now,
            )
    }

    fn hit(
        &self,
        path: String,
        document: &Document,
        found: (Excerpt, Vec<String>),
        passages: Option<Vec<Passage>>,
    ) -> Hit {
        let envelope = &document.envelope;
        Hit {
            scope: self.scope.into(),
            path,
            version: envelope.version,
            created_at: envelope.created_at.utc().z(),
            updated_at: envelope.updated_at.utc().z(),
            actor: envelope.actor_value(),
            matched_fields: found.1,
            excerpt: found.0,
            score: None,
            passages,
            reference: None,
            source: None,
            workspace_id: None,
        }
    }

    /// Pass one. False means the lookahead proved that the result page is complete.
    pub fn push(&mut self, path: String, document: Document) -> Result<bool, Error> {
        if !self.eligible(&path, &document) {
            return Ok(true);
        }
        let request = self.request;
        match self.order {
            Order::Relevance => {
                let tally =
                    matching::tally(self.scope, &path, &document, request, &self.terms, true)?;
                self.stats.add(&tally.counts, tally.length);
                if tally.counts.iter().any(|c| *c > 0) {
                    let key = self.paths.keep(&path);
                    self.facts
                        .keep(key, &tally.counts, tally.length)
                        .map_err(|broad| {
                            Error::Value(crate::limits::broad(
                                broad.0,
                                "documents",
                                "the words, the path or the update dates",
                            ))
                        })?;
                }
                Ok(true)
            }
            Order::Newest => {
                let tally =
                    matching::tally(self.scope, &path, &document, request, &self.terms, false)?;
                if tally.all {
                    let key = NewestKey {
                        path: path.into_boxed_str(),
                        updated: document.envelope.updated_at.instant(),
                    };
                    self.newest.offer(key, newest);
                }
                Ok(true)
            }
            Order::Path => {
                let measured =
                    matching::measure(self.scope, &path, &document, request, &self.terms, false)?;
                let Some(found) = measured.found.filter(|_| measured.all) else {
                    return Ok(true);
                };
                if self.page.hits.len() == request.limit {
                    self.page.next_cursor = Some(request.cursor_for(
                        &json!(self.scope),
                        "memory-search-v1",
                        &self.page.hits.last().expect("nonempty").path,
                    ));
                    return Ok(false);
                }
                let hit = self.hit(path, &document, found, measured.passages);
                self.page.hits.push(hit);
                Ok(true)
            }
        }
    }

    /// After pass one: the winners' paths in answer order, for the backend to
    /// read again in the same boundary. Empty for path order.
    pub fn winners(&mut self) -> Vec<String> {
        let facts = std::mem::replace(&mut self.facts, Facts::new(0, 0));
        let newest = std::mem::replace(&mut self.newest, Best::new(0));
        let paths = std::mem::take(&mut self.paths);
        self.winners = match self.order {
            Order::Relevance => facts
                .best(&self.stats, self.request.limit, |a, b| {
                    paths.get(a).cmp(paths.get(b))
                })
                .into_iter()
                .map(|(key, score)| (paths.get(&key).to_owned(), Some(score)))
                .collect(),
            Order::Newest => newest
                .into_vec(self::newest)
                .into_iter()
                .map(|k| (k.path.into_string(), None))
                .collect(),
            Order::Path => Vec::new(),
        };
        self.winners.iter().map(|(path, _)| path.clone()).collect()
    }

    /// Pass two: one winner read again; only now are its excerpt and passages built.
    pub fn build(&mut self, path: String, document: Document) -> Result<(), Error> {
        let Some(score) = self
            .winners
            .iter()
            .find(|(winner, _)| *winner == path)
            .map(|(_, score)| *score)
        else {
            return Ok(());
        };
        let any = self.order == Order::Relevance;
        let measured =
            matching::measure(self.scope, &path, &document, self.request, &self.terms, any)?;
        let found = measured
            .found
            .ok_or_else(|| std::io::Error::other("a search winner changed while it was read"))?;
        let mut hit = self.hit(path.clone(), &document, found, measured.passages);
        hit.score = score;
        self.built.insert(path, hit);
        Ok(())
    }

    pub fn finish(mut self) -> Result<Page, Error> {
        if !self.ranked() {
            return Ok(self.page);
        }
        let mut hits = Vec::with_capacity(self.winners.len());
        for (path, _) in &self.winners {
            let hit = self.built.remove(path).ok_or_else(|| {
                std::io::Error::other("a search winner vanished while it was read")
            })?;
            hits.push(hit);
        }
        self.page.hits = hits;
        Ok(self.page)
    }
}

/// Newest order: `updated_at` descending, then path ascending.
fn newest(a: &NewestKey, b: &NewestKey) -> std::cmp::Ordering {
    (b.updated, &a.path).cmp(&(a.updated, &b.path))
}

/// Both passes over documents in path order; `load` reads one winner again
/// inside the same read boundary.
pub fn scan(
    scope: &str,
    request: &Request,
    documents: impl Iterator<Item = Result<(String, Document), Error>>,
    mut load: impl FnMut(&str) -> Result<Document, Error>,
) -> Result<Page, Error> {
    let mut scan = Scan::new(scope, request)?;
    for row in documents {
        let (path, document) = row?;
        if !scan.push(path, document)? {
            break;
        }
    }
    for path in scan.winners() {
        let document = load(&path)?;
        scan.build(path, document)?;
    }
    scan.finish()
}
