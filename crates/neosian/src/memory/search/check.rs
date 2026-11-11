//! Validate an optional backend's page before forwarding its references.

use std::cmp::Reverse;
use std::collections::HashSet;

use super::{Hit, Order, Page, Request, searchable};
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::validate_document_path;
use crate::store::time::Stamp;

fn instant(hit: &Hit) -> Option<i64> {
    Stamp::parse(&hit.updated_at).ok().map(Stamp::instant)
}

/// Whether `b` may follow `a` in the requested order; keys are strictly increasing.
fn follows(order: Order, a: &Hit, b: &Hit) -> bool {
    match order {
        Order::Path => a.path < b.path,
        Order::Relevance => (Reverse(a.score), &a.path) < (Reverse(b.score), &b.path),
        Order::Newest => (Reverse(instant(a)), &a.path) < (Reverse(instant(b)), &b.path),
    }
}

impl Request {
    pub fn check_page(&self, scope: &str, page: &Page) -> Result<(), Error> {
        self.validate_backend(scope)?;
        let bad = || MemoryError::configuration("malformed memory-search version 1 response");
        let (since, before) = self.bounds().map_err(Error::Value)?;
        let order = self.order.unwrap_or(Order::Path);
        if page.hits.len() > self.limit || page.order != self.order || page.fallback.is_some() {
            return Err(bad().into());
        }
        let mut seen = HashSet::new();
        let mut previous: Option<&Hit> = None;
        for hit in &page.hits {
            let updated = instant(hit);
            if hit.scope != scope
                || !seen.insert(hit.path.as_str())
                || previous.is_some_and(|p| !follows(order, p, hit))
                || !hit.path.starts_with(&self.prefix)
                || !searchable(&hit.path)
                || validate_document_path(&hit.path).is_err()
                || hit.version == 0
                || hit.reference.is_some()
                || hit.source.is_some()
                || hit.workspace_id.is_some()
                || (order == Order::Relevance) != hit.score.is_some()
                || hit.excerpt.text.chars().count() > 240
                || !self.fields.contains(&hit.excerpt.field)
                || hit.matched_fields.is_empty()
                || hit
                    .matched_fields
                    .iter()
                    .enumerate()
                    .any(|(i, f)| !self.fields.contains(f) || hit.matched_fields[..i].contains(f))
                || !hit.matched_fields.contains(&hit.excerpt.field)
                || Stamp::parse(&hit.created_at).is_err()
                || updated.is_none()
                || since.is_some_and(|s| updated.is_some_and(|u| u < s))
                || before.is_some_and(|b| updated.is_some_and(|u| u >= b))
                || !hit.created_at.ends_with('Z')
                || !hit.updated_at.ends_with('Z')
                || match &hit.excerpt.metadata_key {
                    Some(key) => {
                        hit.excerpt.field != "metadata" || !self.metadata_keys.contains(key)
                    }
                    None => hit.excerpt.field == "metadata",
                }
            {
                return Err(bad().into());
            }
            previous = Some(hit);
        }
        if let Some(next) = &page.next_cursor
            && (order.ranked()
                || page.hits.len() != self.limit
                || next.is_empty()
                || next.len() > 4096
                || self.cursor.as_ref() == Some(next))
        {
            return Err(bad().into());
        }
        Ok(())
    }
}
