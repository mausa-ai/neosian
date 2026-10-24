//! Scoped source routing. Storage sees its original scopes and unchanged records.

use super::files::Endpoint;
use super::model::{Access, Profile};
use super::resolve::Resolved;
use crate::conversation::message::Message;
use crate::conversation::{self, Projection, Redaction, Turn};
use crate::state::{Deleted, State};
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::Actor;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, Restored};
use std::collections::BTreeMap;
use std::sync::Arc;

struct Routed {
    profile: Profile,
    sources: BTreeMap<String, Arc<dyn State>>,
}

fn denied() -> Error {
    MemoryError::configuration("operation is outside the selected profile").into()
}

impl Resolved {
    pub fn open(&self) -> Result<Arc<dyn State>, Error> {
        self.check().map_err(MemoryError::configuration)?;
        let mut selections = BTreeMap::new();
        let env = |key: &str| {
            std::env::var(key)
                .ok()
                .filter(|v| !v.is_empty())
                .ok_or_else(|| {
                    MemoryError::configuration(format!(
                        "required credential variable {key} is unset"
                    ))
                })
        };
        for id in &self.profile.sources {
            use crate::remote::selection::Selection;
            let selected = match &self.bindings[id].endpoint {
                Endpoint::File { root } => Selection::File(root.clone()),
                Endpoint::Http { url, token_env } => Selection::Remote {
                    url: url.clone(),
                    token: env(token_env)?,
                },
                Endpoint::Postgres { dsn_env, schema } => Selection::Postgres {
                    dsn: env(dsn_env)?,
                    schema: schema.clone(),
                },
            };
            selections.insert(id.clone(), selected);
        }
        let mut sources = BTreeMap::new();
        for (id, selection) in selections {
            let store: Arc<dyn State> = match &selection {
                crate::remote::selection::Selection::Remote { url, token } => {
                    Arc::new(crate::remote::RemoteStore::profile_connect(url, token)?)
                }
                _ => selection.open()?,
            };
            sources.insert(id, store);
        }
        Ok(Arc::new(Routed {
            profile: self.profile.clone(),
            sources,
        }))
    }
}

impl Routed {
    fn memory(&self, scope: &str, write: bool, structural: bool) -> Result<&dyn State, Error> {
        if let Some(mount) = self.profile.mounts.iter().find(|m| m.scope == scope) {
            if write && mount.access == Access::Ro || structural && mount.access == Access::Eo {
                return Err(denied());
            }
            return Ok(self.sources[&mount.source].as_ref());
        }
        if let Some(h) = &self.profile.history
            && h.scope == scope
        {
            if write && self.profile.capture.mode == super::model::CaptureMode::Off {
                return Err(denied());
            }
            return Ok(self.sources[&h.source].as_ref());
        }
        Err(denied())
    }

    fn history(&self, id: &str, write: bool) -> Result<&dyn State, conversation::Error> {
        crate::conversation::ids::parse_conversation_id(id)?;
        let h = self.profile.history.as_ref().ok_or_else(denied)?;
        let allowed = h.prefix.as_ref().is_some_and(|p| id.starts_with(p))
            || h.conversations.iter().any(|c| c == id);
        if !allowed || write && self.profile.capture.mode == super::model::CaptureMode::Off {
            return Err(denied().into());
        }
        Ok(self.sources[&h.source].as_ref())
    }

    fn history_ids(&self) -> Result<Vec<String>, Error> {
        let h = self.profile.history.as_ref().ok_or_else(denied)?;
        if h.prefix.is_none() {
            return Ok(h.conversations.clone());
        }
        let entries = self.sources[&h.source].list_documents(&h.scope, "sessions/")?;
        if entries.len() > 10_000 {
            return Err(MemoryError::configuration(
                "history listing exceeds 10000 sessions; select a conversation explicitly",
            )
            .into());
        }
        Ok(entries
            .into_iter()
            .filter(|e| !e.redacted)
            .filter_map(|e| {
                let id = e.path.strip_prefix("sessions/")?;
                (self.history(id, false).is_ok()).then(|| id.to_string())
            })
            .collect())
    }
}

impl State for Routed {
    fn search_memory(
        &self,
        scope: &str,
        request: &crate::memory::search::Request,
    ) -> Result<crate::memory::search::Page, Error> {
        if !self.profile.mounts.iter().any(|m| m.scope == scope) {
            return Err(denied());
        }
        self.memory(scope, false, false)?
            .search_memory(scope, request)
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
            .all(|s| s.supports_optimistic_concurrency())
    }
    fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error> {
        let store = self.memory(scope, false, false)?;
        let history = self
            .profile
            .history
            .as_ref()
            .is_some_and(|h| h.scope == scope);
        if history
            && path
                .strip_prefix("sessions/")
                .is_some_and(|id| self.history(id, false).is_err())
        {
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
        if self
            .profile
            .history
            .as_ref()
            .is_some_and(|h| h.scope == scope)
        {
            return Ok(entries
                .into_iter()
                .filter(|e| {
                    e.path
                        .strip_prefix("sessions/")
                        .is_none_or(|id| self.history(id, false).is_ok())
                })
                .collect());
        }
        Ok(entries)
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
        let found;
        let ids = match ids {
            Some(ids) => ids,
            None => {
                found = self.history_ids()?;
                &found
            }
        };
        for id in ids {
            self.history(id, false)?;
        }
        if ids.is_empty() {
            return Ok(vec![]);
        }
        self.history(&ids[0], false)?
            .search_turns(terms, Some(ids), limit)
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
        let found;
        let ids = match ids {
            Some(ids) => ids,
            None => {
                found = self.history_ids()?;
                &found
            }
        };
        for id in ids {
            self.history(id, false)?;
        }
        if ids.is_empty() {
            return Ok(vec![]);
        }
        self.history(&ids[0], false)?
            .turn_redactions(Some(ids), since, limit)
    }
}
