//! FileStore implements the shared door interface over its existing primitives.

use super::*;
use crate::store::FileStore;

impl State for FileStore {
    fn supports_changes(&self) -> Result<bool, Error> {
        Ok(true)
    }
    fn read_changes(
        &self,
        scope: &str,
        request: &crate::changes::model::Request,
    ) -> Result<crate::changes::model::Page, Error> {
        self.read_changes(scope, request)
    }
    fn prune_changes(
        &self,
        scope: &str,
        through: &str,
        write: bool,
    ) -> Result<crate::changes::model::Pruned, Error> {
        self.prune_changes(scope, through, write)
    }
    fn search_memory(
        &self,
        scope: &str,
        request: &crate::memory::search::Request,
    ) -> Result<crate::memory::search::Page, Error> {
        self.search_memory(scope, request)
    }
    fn supports_optimistic_concurrency(&self) -> bool {
        true
    }
    fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error> {
        self.read(scope, path)
    }
    fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error> {
        self.list_documents(scope, prefix)
    }
    fn document_page(
        &self,
        scope: &str,
        prefix: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<crate::store::Page<Entry>, Error> {
        self.list_documents_page(scope, prefix, cursor, limit)
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
    fn history(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Row>, Error> {
        let mut rows = self.history(scope, since)?;
        if let Some(limit) = limit {
            rows.truncate(limit);
        }
        Ok(rows)
    }
    fn redactions(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Act>, Error> {
        let mut rows = self.redactions(scope, since)?;
        if let Some(limit) = limit {
            rows.truncate(limit);
        }
        Ok(rows)
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
    fn first_turn_stamp(&self, id: &str) -> Result<Option<Stamp>, conversation::Error> {
        self.first_turn_stamp(id)
    }
}
