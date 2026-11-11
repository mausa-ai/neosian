//! Relevance and newest across mounts. Each selected mount answers its own
//! bounded list from its own statistics, because scores computed over
//! different corpora are not comparable. Relevance merges by within-mount rank
//! (reciprocal rank fusion where every document belongs to one list); newest
//! merges by update time. Ties go to mount name, then path.

use super::{Order, Page, Request};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::Mount;
use crate::store::error::Error;
use crate::store::time::Stamp;

pub fn run(
    memory: &Memory,
    selected: &[(&Mount, String)],
    request: &Request,
    order: Order,
) -> Result<Page, Error> {
    let profile = memory.store.profile();
    let mut merged = Vec::new();
    for (mount, prefix) in selected {
        let mut query = request.clone();
        query.prefix.clone_from(prefix);
        query.cursor = None;
        query.knowledge_policy = request
            .knowledge_policy
            .selected(memory.store.as_ref(), &mount.scope);
        let page = memory.store.search_memory(&mount.scope, &query)?;
        query.check_page(&mount.scope, &page)?;
        for (rank, mut hit) in page.hits.into_iter().enumerate() {
            super::mounted::annotate(&mut hit, mount, profile);
            let updated = Stamp::parse(&hit.updated_at).map_or(0, Stamp::instant);
            merged.push((rank, mount.path.clone(), updated, hit));
        }
    }
    if order == Order::Newest {
        merged.sort_by(|a, b| (b.2, &a.1, &a.3.path).cmp(&(a.2, &b.1, &b.3.path)));
    } else {
        merged.sort_by(|a, b| (a.0, &a.1, &a.3.path).cmp(&(b.0, &b.1, &b.3.path)));
    }
    merged.truncate(request.limit);
    let mut page = Page::empty(Some(order));
    page.hits = merged.into_iter().map(|(_, _, _, hit)| hit).collect();
    Ok(page)
}
