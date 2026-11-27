//! Scoped source routing. Storage sees its original scopes and unchanged records.
//! What the history selection admits is `routing/history.rs`; the sources
//! are opened by `routing/open.rs`; record methods go through
//! `routing/records.rs` under the same authority.

mod history;
mod open;
pub mod records;
#[cfg(test)]
mod tests;

use super::model::{Access, Profile};
use crate::conversation::message::Message;
use crate::conversation::{self, Projection, Redaction, Turn};
use crate::state::records::Source;
use crate::state::{Deleted, State};
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::Actor;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, Restored};
use std::collections::BTreeMap;

struct Routed {
    profile: Profile,
    sources: BTreeMap<String, Source>,
}

fn denied() -> Error {
    MemoryError::configuration("operation is outside the selected profile").into()
}

impl Routed {
    fn memory(&self, scope: &str, write: bool, structural: bool) -> Result<&dyn State, Error> {
        if let Some(mount) = self.profile.mounts.iter().find(|m| m.scope == scope) {
            if write && mount.access == Access::Ro || structural && mount.access == Access::Eo {
                return Err(denied());
            }
            return Ok(self.sources[&mount.source].state());
        }
        if let Some(h) = &self.profile.history
            && h.scope == scope
        {
            if write && self.profile.capture.mode == super::model::CaptureMode::Off {
                return Err(denied());
            }
            return Ok(self.sources[&h.source].state());
        }
        Err(denied())
    }
}

impl State for Routed {
    fn read_changes(
        &self,
        scope: &str,
        request: &crate::changes::model::Request,
    ) -> Result<crate::changes::model::Page, Error> {
        if !self.profile.mounts.iter().any(|m| m.scope == scope) {
            return Err(denied());
        }
        self.memory(scope, false, false)?
            .read_changes(scope, request)
    }
    fn prune_changes(
        &self,
        scope: &str,
        through: &str,
        write: bool,
    ) -> Result<crate::changes::model::Pruned, Error> {
        if !self.profile.mounts.iter().any(|m| m.scope == scope) {
            return Err(denied());
        }
        self.memory(scope, true, true)?
            .prune_changes(scope, through, write)
    }
    fn memory_ranking(&self, scope: &str) -> bool {
        self.profile.mounts.iter().any(|m| m.scope == scope)
            && self
                .memory(scope, false, false)
                .is_ok_and(|backend| backend.memory_ranking(scope))
    }
    fn memory_passages(&self, scope: &str) -> bool {
        self.profile.mounts.iter().any(|m| m.scope == scope)
            && self
                .memory(scope, false, false)
                .is_ok_and(|backend| backend.memory_passages(scope))
    }
    fn search_memory(
        &self,
        scope: &str,
        request: &crate::memory::search::Request,
    ) -> Result<crate::memory::search::Page, Error> {
        if !self.profile.mounts.iter().any(|m| m.scope == scope) {
            return Err(denied());
        }
        let mut request = request.clone();
        request.knowledge_policy = request.knowledge_policy.selected(self, scope);
        self.memory(scope, false, false)?
            .search_memory(scope, &request)
    }
    fn profile(&self) -> Option<&Profile> {
        Some(&self.profile)
    }
    fn same_source(&self, a: &str, b: &str) -> bool {
        let source = |scope| {
            self.profile
                .mounts
                .iter()
                .find(|m| m.scope == scope)
                .map(|m| &m.source)
        };
        source(a).is_some() && source(a) == source(b)
    }
    fn supports_optimistic_concurrency(&self) -> bool {
        self.sources
            .values()
            .all(|s| s.state().supports_optimistic_concurrency())
    }
    fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error> {
        let store = self.memory(scope, false, false)?;
        let history = self.history_scope(scope);
        if history && !self.visible(path) {
            return Ok(None);
        }
        let doc = store.read(scope, path)?;
        if history && path == crate::conversation::sessions::HANDOFF_PATH {
            let visible = doc
                .as_ref()
                .and_then(|d| crate::conversation::sessions::parse_handoff(&d.content))
                .is_some_and(|h| {
                    h.conversation.as_deref().map_or(
                        self.profile.capture.mode != super::model::CaptureMode::Off,
                        |id| self.history(id, false).is_ok(),
                    )
                });
            if !visible {
                return Ok(None);
            }
        }
        Ok(doc)
    }
    fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error> {
        let entries = self
            .memory(scope, false, false)?
            .list_documents(scope, prefix)?;
        if self.history_scope(scope) {
            return Ok(entries
                .into_iter()
                .filter(|e| self.visible(&e.path))
                .collect());
        }
        Ok(entries)
    }
    fn document_page(
        &self,
        scope: &str,
        prefix: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<crate::store::Page<Entry>, Error> {
        let mut page = self
            .memory(scope, false, false)?
            .document_page(scope, prefix, cursor, limit)?;
        if self.history_scope(scope) {
            page.items.retain(|e| self.visible(&e.path));
        }
        Ok(page)
    }
    fn versions(&self, scope: &str, path: &str, limit: usize) -> Result<Vec<Row>, Error> {
        self.memory(scope, false, false)?
            .versions(scope, path, limit)
    }
    fn write(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<u64, Error> {
        let store = self.memory(scope, true, false)?;
        if self
            .profile
            .mounts
            .iter()
            .any(|m| m.scope == scope && m.access == Access::Eo)
            && store.read(scope, path)?.is_none()
        {
            return Err(denied());
        }
        store.write(scope, path, content, actor, expected)
    }
    fn delete(&self, scope: &str, path: &str, actor: &Actor) -> Result<Deleted, Error> {
        self.memory(scope, true, true)?.delete(scope, path, actor)
    }
    fn rename(&self, scope: &str, src: &str, dst: &str, actor: &Actor) -> Result<u64, Error> {
        self.memory(scope, true, true)?
            .rename(scope, src, dst, actor)
    }
    fn read_turns(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Turn>, conversation::Error> {
        self.history(id, false)?.read_turns(id, after, limit)
    }
    fn read_projections(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Projection>, conversation::Error> {
        self.history(id, false)?.read_projections(id, after, limit)
    }
    fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, conversation::Error> {
        let mut found = Vec::new();
        self.search_turns_each(terms, ids, limit, &mut |turn| {
            found.push(turn);
            Ok(true)
        })?;
        Ok(found)
    }
    fn search_turns_each(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        visit: &mut dyn FnMut(Turn) -> Result<bool, conversation::Error>,
    ) -> Result<(), conversation::Error> {
        let ids = self.authorized(ids)?;
        let Some(first) = ids.first() else {
            return Ok(());
        };
        self.history(first, false)?
            .search_turns_each(terms, Some(&ids), limit, visit)
    }
    fn read_turns_each(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, conversation::Error>,
    ) -> Result<(), conversation::Error> {
        self.history(id, false)?
            .read_turns_each(id, after, limit, visit)
    }
    fn history_ranking(&self) -> bool {
        self.profile
            .history
            .as_ref()
            .is_some_and(|h| self.sources[&h.source].state().history_ranking())
    }
    fn rank_turns(
        &self,
        request: &crate::conversation::rank::Request,
    ) -> Result<Vec<crate::conversation::rank::Ranked>, conversation::Error> {
        let mut ranked = Vec::new();
        self.rank_turns_each(request, &mut |turn, score| {
            ranked.push((turn, score));
            Ok(true)
        })?;
        Ok(ranked)
    }
    fn rank_turns_each(
        &self,
        request: &crate::conversation::rank::Request,
        visit: &mut crate::conversation::page::Visit,
    ) -> Result<(), conversation::Error> {
        let ids = self.authorized(request.ids.as_deref())?;
        let Some(first) = ids.first() else {
            return Ok(());
        };
        let mut scoped = request.clone();
        scoped.ids = Some(ids.clone());
        self.history(first, false)?.rank_turns_each(&scoped, visit)
    }
    fn append_turn(
        &self,
        id: &str,
        messages: Vec<Message>,
        actor: Option<String>,
    ) -> Result<Turn, conversation::Error> {
        self.history(id, true)?.append_turn(id, messages, actor)
    }
    fn history(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Row>, Error> {
        self.memory(scope, false, false)?
            .history(scope, since, limit)
    }
    fn redactions(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Act>, Error> {
        self.memory(scope, false, false)?
            .redactions(scope, since, limit)
    }
    fn redact(&self, scope: &str, path: Option<&str>, actor: &Actor) -> Result<usize, Error> {
        self.memory(scope, true, true)?.redact(scope, path, actor)
    }
    fn scopes(&self) -> Result<Vec<String>, Error> {
        Ok(self
            .profile
            .mounts
            .iter()
            .map(|m| m.scope.clone())
            .collect())
    }
    fn conversations(&self) -> Result<Vec<String>, Error> {
        self.history_ids()
    }
    fn last_turn_number(&self, id: &str) -> Result<u64, conversation::Error> {
        self.history(id, false)?.last_turn_number(id)
    }
    fn restore_scope(&self, _: &str, _: &[Restored], _: Vec<Row>, _: &[Act]) -> Result<(), Error> {
        Err(denied())
    }
    fn restore_conversation(
        &self,
        _: &str,
        _: &[Turn],
        _: &[Projection],
        _: &[Redaction],
    ) -> Result<(), conversation::Error> {
        Err(denied().into())
    }
    fn redact_turns(
        &self,
        id: &str,
        through: Option<i64>,
        turns: Option<&[i64]>,
        actor: Option<&str>,
    ) -> Result<usize, conversation::Error> {
        self.history(id, true)?
            .redact_turns(id, through, turns, actor)
    }
    fn turn_redactions(
        &self,
        ids: Option<&[String]>,
        since: Option<Stamp>,
        limit: i64,
    ) -> Result<Vec<Redaction>, conversation::Error> {
        let ids = self.authorized(ids)?;
        let Some(first) = ids.first() else {
            return Ok(vec![]);
        };
        self.history(first, false)?
            .turn_redactions(Some(&ids), since, limit)
    }
}
