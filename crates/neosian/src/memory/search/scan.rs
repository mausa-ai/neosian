//! Incremental matching under the backend's stable read boundary.
//!
//! Path order streams in path order and stops after its lookahead. Relevance
//! and newest read the whole eligible selection, then sort and cut to `limit`.

use super::{Excerpt, Hit, Order, Page, Request, matching, rank::Stats, searchable};
use crate::store::{Document, error::Error, time::Stamp};
use serde_json::json;

struct Candidate {
    hit: Hit,
    counts: Vec<u32>,
    length: u64,
    updated: i64,
}

pub struct Scan<'a> {
    scope: &'a str,
    request: &'a Request,
    after: String,
    terms: Vec<String>,
    now: Stamp,
    bounds: (Option<i64>, Option<i64>),
    page: Page,
    stats: Stats,
    candidates: Vec<Candidate>,
}

impl<'a> Scan<'a> {
    pub fn new(scope: &'a str, request: &'a Request) -> Result<Self, Error> {
        let terms = matching::terms(&request.query);
        Ok(Self {
            scope,
            request,
            after: request.scope_after(scope)?,
            bounds: request.bounds().map_err(Error::Value)?,
            stats: Stats::new(terms.len()),
            terms,
            now: Stamp::now(),
            page: Page::empty(request.order),
            candidates: Vec::new(),
        })
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

    fn hit(&self, path: String, document: &Document, found: (Excerpt, Vec<String>)) -> Hit {
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
            reference: None,
            source: None,
            workspace_id: None,
        }
    }

    /// False means the lookahead proved that the result page is complete.
    pub fn push(&mut self, path: String, document: Document) -> Result<bool, Error> {
        if !self.eligible(&path, &document) {
            return Ok(true);
        }
        let request = self.request;
        let order = request.order.unwrap_or(Order::Path);
        let measured = matching::measure(
            self.scope,
            &path,
            &document,
            request,
            &self.terms,
            order == Order::Relevance,
        )?;
        if order == Order::Relevance {
            self.stats.add(&measured.counts, measured.length);
        }
        let keep = if order == Order::Relevance {
            measured.found
        } else {
            measured.found.filter(|_| measured.all)
        };
        let Some(found) = keep else {
            return Ok(true);
        };
        if order.ranked() {
            let updated = document.envelope.updated_at.instant();
            let hit = self.hit(path, &document, found);
            self.candidates.push(Candidate {
                hit,
                counts: measured.counts,
                length: measured.length,
                updated,
            });
            return Ok(true);
        }
        if self.page.hits.len() == request.limit {
            self.page.next_cursor = Some(request.cursor_for(
                &json!(self.scope),
                "memory-search-v1",
                &self.page.hits.last().expect("nonempty").path,
            ));
            return Ok(false);
        }
        let hit = self.hit(path, &document, found);
        self.page.hits.push(hit);
        Ok(true)
    }

    pub fn finish(mut self) -> Page {
        match self.request.order {
            Some(Order::Relevance) => {
                for candidate in &mut self.candidates {
                    let score = self.stats.score(&candidate.counts, candidate.length);
                    candidate.hit.score = Some(score);
                }
                self.candidates
                    .sort_by(|a, b| (b.hit.score, &a.hit.path).cmp(&(a.hit.score, &b.hit.path)));
            }
            Some(Order::Newest) => self
                .candidates
                .sort_by(|a, b| (b.updated, &a.hit.path).cmp(&(a.updated, &b.hit.path))),
            _ => return self.page,
        }
        self.candidates.truncate(self.request.limit);
        self.page.hits = self.candidates.into_iter().map(|c| c.hit).collect();
        self.page
    }
}

pub fn scan(
    scope: &str,
    request: &Request,
    documents: impl Iterator<Item = Result<(String, Document), Error>>,
) -> Result<Page, Error> {
    let mut scan = Scan::new(scope, request)?;
    for row in documents {
        let (path, document) = row?;
        if !scan.push(path, document)? {
            break;
        }
    }
    Ok(scan.finish())
}
