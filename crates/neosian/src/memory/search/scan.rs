//! Incremental matching under the backend's stable read boundary.

use super::{Hit, Page, Request, matching, searchable};
use crate::store::{Document, error::Error, time::Stamp};
use serde_json::json;

pub struct Scan<'a> {
    scope: &'a str,
    request: &'a Request,
    after: String,
    terms: Vec<String>,
    now: Stamp,
    page: Page,
}

impl<'a> Scan<'a> {
    pub fn new(scope: &'a str, request: &'a Request) -> Result<Self, Error> {
        Ok(Self {
            scope,
            request,
            after: request.scope_after(scope)?,
            terms: matching::terms(&request.query),
            now: Stamp::now(),
            page: Page {
                hits: Vec::new(),
                next_cursor: None,
            },
        })
    }
    /// False means the lookahead proved that the result page is complete.
    pub fn push(&mut self, path: String, document: Document) -> Result<bool, Error> {
        let request = self.request;
        if path <= self.after
            || !path.starts_with(&request.prefix)
            || !searchable(&path)
            || document.envelope.redacted
            || !crate::knowledge::governance::eligible(
                request.knowledge_policy,
                self.scope,
                &path,
                &document.content,
                self.now,
            )
        {
            return Ok(true);
        }
        if let Some((excerpt, matched_fields)) =
            matching::find(self.scope, &path, &document, request, &self.terms)?
        {
            if self.page.hits.len() == request.limit {
                self.page.next_cursor = Some(request.cursor_for(
                    &json!(self.scope),
                    "memory-search-v1",
                    &self.page.hits.last().expect("nonempty").path,
                ));
                return Ok(false);
            }
            let envelope = &document.envelope;
            self.page.hits.push(Hit {
                scope: self.scope.into(),
                path,
                version: envelope.version,
                created_at: envelope.created_at.utc().z(),
                updated_at: envelope.updated_at.utc().z(),
                actor: envelope.actor_value(),
                matched_fields,
                excerpt,
                reference: None,
                source: None,
                workspace_id: None,
            });
        }
        Ok(true)
    }
    pub fn finish(self) -> Page {
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
