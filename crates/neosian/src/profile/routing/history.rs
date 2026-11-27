//! What a profile's history selection admits: one conversation by its
//! literal prefix or by name, its writes only while capture is on, and,
//! when a caller names none, the selection's own conversations found from
//! the sessions documents of its history scope. Every history method, the
//! legacy ones and the record ones, is admitted here before its source is
//! asked anything.

use super::{Routed, denied};
use crate::conversation;
use crate::profile::model::CaptureMode;
use crate::state::State;
use crate::state::records::Source;
use crate::store::error::{Error, MemoryError};

impl Routed {
    /// The history source, once the selection admits `id`, for writing
    /// only while capture is on.
    pub(super) fn admitted(&self, id: &str, write: bool) -> Result<&Source, conversation::Error> {
        crate::conversation::ids::parse_conversation_id(id)?;
        let h = self.profile.history.as_ref().ok_or_else(denied)?;
        let allowed = h.prefix.as_ref().is_some_and(|p| id.starts_with(p))
            || h.conversations.iter().any(|c| c == id);
        if !allowed || write && self.profile.capture.mode == CaptureMode::Off {
            return Err(denied().into());
        }
        Ok(&self.sources[&h.source])
    }

    pub(super) fn history(&self, id: &str, write: bool) -> Result<&dyn State, conversation::Error> {
        Ok(self.admitted(id, write)?.state())
    }

    /// Whether `scope` is the history scope, whose sessions documents are
    /// filtered by the selection.
    pub(super) fn history_scope(&self, scope: &str) -> bool {
        self.profile
            .history
            .as_ref()
            .is_some_and(|h| h.scope == scope)
    }

    /// Whether a document of the history scope is visible: any document
    /// but a sessions document of a conversation the selection excludes.
    pub(super) fn visible(&self, path: &str) -> bool {
        path.strip_prefix("sessions/")
            .is_none_or(|id| self.history(id, false).is_ok())
    }

    pub(super) fn history_ids(&self) -> Result<Vec<String>, Error> {
        let h = self.profile.history.as_ref().ok_or_else(denied)?;
        if h.prefix.is_none() {
            return Ok(h.conversations.clone());
        }
        let entries = self.sources[&h.source]
            .state()
            .list_documents(&h.scope, "sessions/")?;
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

    /// The conversations a history method may reach: those named, each
    /// admitted, or the selection's own when none are named. An empty
    /// answer means nothing is asked of the source.
    pub(super) fn authorized(
        &self,
        ids: Option<&[String]>,
    ) -> Result<Vec<String>, conversation::Error> {
        let ids = match ids {
            Some(ids) => ids.to_vec(),
            None => self.history_ids()?,
        };
        for id in &ids {
            self.history(id, false)?;
        }
        Ok(ids)
    }
}
