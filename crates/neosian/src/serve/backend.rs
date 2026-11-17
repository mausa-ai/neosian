//! The server's extra callers: document snapshots and bounded cursor pages.
use crate::conversation::page::{Mode, Resume, Visit};
use crate::conversation::rank::Request;
use crate::conversation::{self, Projection, Turn};
use crate::postgres::PostgresStore;
use crate::state::State;
use crate::store::error::Error;
use crate::store::grammar::Actor;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, FileStore, Page};

pub trait Backend: State {
    fn backend(&self) -> &'static str;
    fn write_document(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<Document, Error>;
    fn rename_document(
        &self,
        scope: &str,
        src: &str,
        dst: &str,
        actor: &Actor,
    ) -> Result<Document, Error>;
    /// Validate a zero-limit document listing without fetching entries.
    fn check_document_listing(&self, scope: &str, prefix: &str) -> Result<(), Error>;
    fn versions_page(
        &self,
        scope: &str,
        path: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Row>, Error>;
    fn history_page(
        &self,
        scope: &str,
        since: Option<Stamp>,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Row>, Error>;
    fn redactions_page(
        &self,
        scope: &str,
        since: Option<Stamp>,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Act>, Error>;
    fn append_projections(
        &self,
        id: &str,
        entries: &[Projection],
    ) -> Result<(), conversation::Error>;
    /// One size page of `read_turns` from a store position `hint` (files:
    /// where the next row starts), answering the position of the first turn
    /// `visit` declined (§40.46).
    fn read_turns_page(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        hint: Option<(u64, u64)>,
        mode: Mode,
        visit: &mut dyn FnMut(Turn) -> Result<bool, conversation::Error>,
    ) -> Result<Option<(u64, u64)>, conversation::Error>;
    /// History search handing each turn to `visit` from `resume` (§40.46);
    /// relevance answers its statistics digest.
    fn rank_page(
        &self,
        request: &Request,
        resume: &Resume,
        mode: Mode,
        visit: &mut Visit,
    ) -> Result<Option<String>, conversation::Error>;
}

macro_rules! backend {
    ($store:ty, $listing:expr, $write:expr, $page:expr) => {
        impl Backend for $store {
            fn backend(&self) -> &'static str {
                stringify!($store)
            }
            fn write_document(
                &self,
                scope: &str,
                path: &str,
                content: &str,
                actor: &Actor,
                expected: Option<u64>,
            ) -> Result<Document, Error> {
                ($write)(self, scope, path, content, actor, expected)
            }
            fn rename_document(
                &self,
                scope: &str,
                src: &str,
                dst: &str,
                actor: &Actor,
            ) -> Result<Document, Error> {
                self.rename_document(scope, src, dst, actor)
            }
            fn check_document_listing(&self, scope: &str, prefix: &str) -> Result<(), Error> {
                ($listing)(self, scope, prefix)
            }
            fn versions_page(
                &self,
                scope: &str,
                path: &str,
                cursor: Option<&str>,
                limit: i64,
            ) -> Result<Page<Row>, Error> {
                self.versions_page(scope, path, cursor, limit)
            }
            fn history_page(
                &self,
                scope: &str,
                since: Option<Stamp>,
                cursor: Option<&str>,
                limit: i64,
            ) -> Result<Page<Row>, Error> {
                self.history_page(scope, since, cursor, limit)
            }
            fn redactions_page(
                &self,
                scope: &str,
                since: Option<Stamp>,
                cursor: Option<&str>,
                limit: i64,
            ) -> Result<Page<Act>, Error> {
                self.redactions_page(scope, since, cursor, limit)
            }
            fn append_projections(
                &self,
                id: &str,
                entries: &[Projection],
            ) -> Result<(), conversation::Error> {
                self.append_projections(id, entries)
            }
            fn rank_page(
                &self,
                request: &Request,
                resume: &Resume,
                mode: Mode,
                visit: &mut Visit,
            ) -> Result<Option<String>, conversation::Error> {
                self.rank_each(request, resume, mode, visit)
            }
            fn read_turns_page(
                &self,
                id: &str,
                after: i64,
                limit: Option<i64>,
                hint: Option<(u64, u64)>,
                mode: Mode,
                visit: &mut dyn FnMut(Turn) -> Result<bool, conversation::Error>,
            ) -> Result<Option<(u64, u64)>, conversation::Error> {
                ($page)(self, id, after, limit, hint, mode, visit)
            }
        }
    };
}

backend!(
    FileStore,
    |store: &FileStore, scope: &str, _prefix: &str| store.check_scope(scope),
    |store: &FileStore, scope, path, content: &str, actor, expected| {
        Ok(Document {
            envelope: store.write_document(scope, path, content, actor, expected)?,
            content: content.to_string(),
        })
    },
    |store: &FileStore, id: &str, after, limit, hint, _mode, visit| {
        store.read_turns_page(id, after, limit, hint, visit)
    }
);
// Postgres continues by turn number alone: the keyset is the index.
backend!(
    PostgresStore,
    PostgresStore::check_document_listing,
    PostgresStore::write_document,
    |store: &PostgresStore, id: &str, after, limit, _hint, mode, visit| {
        store
            .read_turns_in(id, after, limit, mode, visit)
            .map(|()| None)
    }
);
