//! The private storage interface used by the product doors.
//! Only operations with a caller belong here; no runtime is required.

mod file;

use crate::conversation::message::Message;
use crate::conversation::{self, Projection, Redaction, Turn};
use crate::store::error::Error;
use crate::store::grammar::Actor;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, Restored};

/// A successful delete may lack a receipt when a remote backend keeps no row.
pub struct Deleted {
    pub removed: bool,
    pub version: Option<u64>,
}

pub trait State: Send + Sync {
    fn supports_changes(&self) -> Result<bool, Error> {
        Ok(false)
    }
    fn read_changes(
        &self,
        _scope: &str,
        _request: &crate::changes::model::Request,
    ) -> Result<crate::changes::model::Page, Error> {
        Err(crate::changes::model::invalid(
            "backend does not support change-feed version 1",
        ))
    }
    fn prune_changes(
        &self,
        _scope: &str,
        _through: &str,
        _write: bool,
    ) -> Result<crate::changes::model::Pruned, Error> {
        Err(crate::changes::model::invalid(
            "backend does not support change-feed version 1",
        ))
    }
    fn profile(&self) -> Option<&crate::profile::Profile> {
        None
    }
    fn same_source(&self, _a: &str, _b: &str) -> bool {
        true
    }
    fn supports_optimistic_concurrency(&self) -> bool;
    fn client(&self) -> Option<&str> {
        None
    }
    fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error>;
    fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error>;
    fn search_memory(
        &self,
        _scope: &str,
        _request: &crate::memory::search::Request,
    ) -> Result<crate::memory::search::Page, Error> {
        Err(crate::store::error::MemoryError::configuration(
            "backend does not support memory-search version 1",
        )
        .into())
    }
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
    fn history(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Row>, Error>;
    fn redactions(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Act>, Error>;
    fn redact(&self, scope: &str, path: Option<&str>, actor: &Actor) -> Result<usize, Error>;
    fn scopes(&self) -> Result<Vec<String>, Error>;
    fn conversations(&self) -> Result<Vec<String>, Error>;
    fn last_turn_number(&self, id: &str) -> Result<u64, conversation::Error>;
    fn restore_scope(
        &self,
        scope: &str,
        documents: &[Restored],
        versions: Vec<Row>,
        redactions: &[Act],
    ) -> Result<(), Error>;
    fn restore_conversation(
        &self,
        id: &str,
        turns: &[Turn],
        projections: &[Projection],
        redactions: &[Redaction],
    ) -> Result<(), conversation::Error>;
    fn redact_turns(
        &self,
        id: &str,
        through: Option<i64>,
        turns: Option<&[i64]>,
        actor: Option<&str>,
    ) -> Result<usize, conversation::Error>;
    fn turn_redactions(
        &self,
        ids: Option<&[String]>,
        since: Option<Stamp>,
        limit: i64,
    ) -> Result<Vec<Redaction>, conversation::Error>;
    fn first_turn_stamp(&self, id: &str) -> Result<Option<Stamp>, conversation::Error> {
        Ok(self
            .read_turns(id, 0, Some(1))?
            .first()
            .map(|turn| turn.created_at))
    }
}
