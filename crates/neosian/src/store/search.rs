//! One lock covers the current-document search page, including its lookahead.

use super::{FileStore, error::Error, layout};
use crate::memory::search::{self, Page, Request};

impl FileStore {
    pub fn search_memory(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        let after = request.scope_after(scope)?;
        let _held = self.lock()?;
        self.scope(scope)?;
        let directory = layout::scope_dir(&self.root, scope).join(layout::DOCUMENTS);
        let mut paths = std::collections::VecDeque::new();
        let mut position = after;
        let mut done = false;
        let rows = std::iter::from_fn(|| {
            loop {
                if paths.is_empty() && !done {
                    match layout::path_page(&directory, ".md", &request.prefix, &position, 256) {
                        Ok(page) => {
                            done = page.len() < 256;
                            if let Some(last) = page.last() {
                                position.clone_from(last);
                            }
                            paths = page.into();
                        }
                        Err(error) => {
                            done = true;
                            return Some(Err(error.into()));
                        }
                    }
                }
                if let Some(path) = paths.pop_front() {
                    if !search::searchable(&path) {
                        continue;
                    }
                    return Some((|| {
                        let doc =
                            self.load(scope, &path, &layout::doc_file(&self.root, scope, &path)?)?;
                        Ok((path, doc))
                    })());
                }
                if done {
                    return None;
                }
            }
        });
        search::scan(scope, request, rows)
    }
}
