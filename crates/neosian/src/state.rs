//! The private storage interface used by the record and MCP doors.
//! Only operations with a caller belong here; operator-only storage stays
//! on FileStore until its remote door arrives. No runtime is required.

use crate::conversation::message::Message;
use crate::conversation::{self, Projection, Turn};
use crate::store::error::Error;
use crate::store::grammar::Actor;
use crate::store::journal::Row;
use crate::store::time::Stamp;
use crate::store::{Document, Entry, FileStore};

/// A successful delete may lack a receipt when a remote backend keeps no row.
pub struct Deleted {
    pub removed: bool,
    pub version: Option<u64>,
}

pub trait State: Send + Sync {
    fn supports_optimistic_concurrency(&self) -> bool;
    fn client(&self) -> Option<&str> {
        None
    }
    fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error>;
    fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error>;
    fn versions(&self, scope: &str, path: &str, limit: usize) -> Result<Vec<Row>, Error>;
    fn write(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<u64, Error>;
    fn delete(&self, scope: &str, path: &str, actor: &Actor) -> Result<Deleted, Error>;
    fn rename(&self, scope: &str, src: &str, dst: &str, actor: &Actor) -> Result<u64, Error>;
    fn read_turns(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Turn>, conversation::Error>;
    fn read_projections(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Projection>, conversation::Error>;
    fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, conversation::Error>;
    fn append_turn(
        &self,
        id: &str,
        messages: Vec<Message>,
        actor: Option<String>,
    ) -> Result<Turn, conversation::Error>;
    fn first_turn_stamp(&self, id: &str) -> Result<Option<Stamp>, conversation::Error> {
        Ok(self
            .read_turns(id, 0, Some(1))?
            .first()
            .map(|turn| turn.created_at))
    }
}

impl State for FileStore {
    fn supports_optimistic_concurrency(&self) -> bool {
        true
    }
    fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error> {
        self.read(scope, path)
    }
    fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error> {
        self.list_documents(scope, prefix)
    }
    fn versions(&self, scope: &str, path: &str, limit: usize) -> Result<Vec<Row>, Error> {
        self.versions(scope, path, limit)
    }
    fn write(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<u64, Error> {
        self.write(scope, path, content, actor, expected)
    }
    fn delete(&self, scope: &str, path: &str, actor: &Actor) -> Result<Deleted, Error> {
        let version = self.delete(scope, path, actor)?;
        Ok(Deleted {
            removed: version.is_some(),
            version,
        })
    }
    fn rename(&self, scope: &str, src: &str, dst: &str, actor: &Actor) -> Result<u64, Error> {
        self.rename(scope, src, dst, actor)
    }
    fn read_turns(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Turn>, conversation::Error> {
        self.read_turns(id, after, limit)
    }
    fn read_projections(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Projection>, conversation::Error> {
        self.read_projections(id, after, limit)
    }
    fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, conversation::Error> {
        self.search_turns(terms, ids, limit)
    }
    fn append_turn(
        &self,
        id: &str,
        messages: Vec<Message>,
        actor: Option<String>,
    ) -> Result<Turn, conversation::Error> {
        self.append_turn(id, messages, actor)
    }
    fn first_turn_stamp(&self, id: &str) -> Result<Option<Stamp>, conversation::Error> {
        self.first_turn_stamp(id)
    }
}
