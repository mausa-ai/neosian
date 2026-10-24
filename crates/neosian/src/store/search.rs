//! One lock covers the current-document search page, including its lookahead.

use super::{FileStore, error::Error, layout};
use crate::memory::search::{self, Page, Request};

impl FileStore {
    pub fn search_memory(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        let after = request.scope_after(scope)?;
        let _held = self.lock()?;
        self.scope(scope)?;
        let directory = layout::scope_dir(&self.root, scope).join(layout::DOCUMENTS);
        let mut paths = layout::logical_paths(&directory, ".md")?;
        paths.sort();
        let rows = paths
            .into_iter()
            .filter(|p| p > &after && p.starts_with(&request.prefix) && search::searchable(p))
            .map(|path| {
                let doc = self.load(scope, &path, &layout::doc_file(&self.root, scope, &path)?)?;
                Ok((path, doc))
            });
        search::scan(scope, request, rows)
    }
}
