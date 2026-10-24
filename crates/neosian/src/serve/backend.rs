//! The server's extra callers: document snapshots and bounded cursor pages.
use crate::conversation::{self, Projection};
use crate::postgres::PostgresStore;
use crate::state::State;
use crate::store::error::Error;
use crate::store::grammar::Actor;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, FileStore, Page};

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
    fn list_documents_page(
        &self,
        scope: &str,
        prefix: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Entry>, Error>;
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
}

macro_rules! backend {
    ($store:ty, $write:expr) => {
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
            fn list_documents_page(
                &self,
                scope: &str,
                prefix: &str,
                cursor: Option<&str>,
                limit: i64,
            ) -> Result<Page<Entry>, Error> {
                self.list_documents_page(scope, prefix, cursor, limit)
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
        }
    };
}

backend!(FileStore, |store: &FileStore,
                     scope,
                     path,
                     content: &str,
                     actor,
                     expected| {
    Ok(Document {
        envelope: store.write_document(scope, path, content, actor, expected)?,
        content: content.to_string(),
    })
});
backend!(PostgresStore, PostgresStore::write_document);
