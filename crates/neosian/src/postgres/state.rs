//! Postgres implements the product doors over the SQL-backed primitives.

use super::PostgresStore;
use crate::conversation::message::Message;
use crate::conversation::{self, Projection, Redaction, Turn};
use crate::state::*;
use crate::store::error::Error;
use crate::store::grammar::Actor;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, Restored};

impl State for PostgresStore {
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
        Ok(self
            .write_document(scope, path, content, actor, expected)?
            .envelope
            .version)
    }
    fn delete(&self, scope: &str, path: &str, actor: &Actor) -> Result<Deleted, Error> {
        self.delete(scope, path, actor)
    }
    fn rename(&self, scope: &str, src: &str, dst: &str, actor: &Actor) -> Result<u64, Error> {
        Ok(self
            .rename_document(scope, src, dst, actor)?
            .envelope
            .version)
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
    fn history(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Row>, Error> {
        self.history(scope, since, limit)
    }
    fn redactions(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Act>, Error> {
        self.redactions(scope, since, limit)
    }
    fn redact(&self, scope: &str, path: Option<&str>, actor: &Actor) -> Result<usize, Error> {
        self.redact(scope, path, actor)
    }
    fn scopes(&self) -> Result<Vec<String>, Error> {
        self.scopes()
    }
    fn conversations(&self) -> Result<Vec<String>, Error> {
        self.conversations()
    }
    fn last_turn_number(&self, id: &str) -> Result<u64, conversation::Error> {
        self.last_turn_number(id)
    }
    fn restore_scope(
        &self,
        scope: &str,
        documents: &[Restored],
        versions: Vec<Row>,
        redactions: &[Act],
    ) -> Result<(), Error> {
        self.restore_scope(scope, documents, versions, redactions)
    }
    fn restore_conversation(
        &self,
        id: &str,
        turns: &[Turn],
        projections: &[Projection],
        redactions: &[Redaction],
    ) -> Result<(), conversation::Error> {
        self.restore_conversation(id, turns, projections, redactions)
    }
    fn redact_turns(
        &self,
        id: &str,
        through: Option<i64>,
        turns: Option<&[i64]>,
        actor: Option<&str>,
    ) -> Result<usize, conversation::Error> {
        self.redact_turns(id, through, turns, actor)
    }
    fn turn_redactions(
        &self,
        ids: Option<&[String]>,
        since: Option<Stamp>,
        limit: i64,
    ) -> Result<Vec<Redaction>, conversation::Error> {
        self.turn_redactions(ids, since, limit)
    }
}
