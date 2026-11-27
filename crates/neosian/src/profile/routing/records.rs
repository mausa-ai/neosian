//! Record methods through a profile (DESIGN §40.54 items 9 to 11 and 17):
//! they reach the profile's history source and no other, under the
//! authority of its legacy history methods. A write needs capture on and
//! a conversation the selection admits for writing, as `append_turn`
//! does. A read needs a conversation the selection admits. A search is
//! limited to the conversations the selection admits, also when the
//! caller names none, so a server is always sent the list; an empty
//! selection answers no hits and asks nothing. A restore and the sweep
//! are the operator's and are refused here, as `restore_scope` and
//! `restore_conversation` are.

use std::sync::Arc;

use super::{Routed, denied};
use crate::conversation;
use crate::conversation::turn_records::sweep::SweepPage;
use crate::conversation::turn_records::{RecordHit, RecordRead, Restore, Restored, Write, Written};
use crate::remote::records::{RecordPage, RecordQuery};
use crate::state::State;
use crate::state::records::{Fault, Keeper, Records, Source};

/// A profile opened for its history records (`Resolved::open_history`).
pub struct HistoryRecords {
    routed: Arc<Routed>,
}

/// The history source's records under the profile's authority.
pub struct Authorized<'a> {
    routed: &'a Routed,
    keeper: Keeper<'a>,
}

impl HistoryRecords {
    pub(super) fn new(routed: Arc<Routed>) -> Self {
        Self { routed }
    }

    /// The routed store, through which every other method goes.
    pub fn state(&self) -> &dyn State {
        self.routed.as_ref()
    }

    /// The history source, once the selection admits `id`; for writing
    /// only while capture is on.
    pub fn source(&self, id: &str, write: bool) -> Result<&Source, conversation::Error> {
        self.routed.admitted(id, write)
    }

    /// The history source's records when it keeps records now.
    pub fn records(&self) -> Result<Option<Authorized<'_>>, conversation::Error> {
        let history = self.routed.profile.history.as_ref().ok_or_else(denied)?;
        let keeper = self.routed.sources[&history.source].keeper()?;
        Ok(keeper.map(|keeper| Authorized {
            routed: &self.routed,
            keeper,
        }))
    }
}

impl Records for Authorized<'_> {
    fn write(&self, id: &str, write: Write) -> Result<Written, Fault> {
        self.routed.admitted(id, true)?;
        self.keeper.write(id, write)
    }

    fn read(
        &self,
        id: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    ) -> Result<(), Fault> {
        self.routed.admitted(id, false)?;
        self.keeper.read(id, read, visit)
    }

    fn search(&self, query: &RecordQuery) -> Result<Vec<RecordHit>, Fault> {
        let conversations = self.routed.authorized(query.conversations.as_deref())?;
        if conversations.is_empty() {
            return Ok(Vec::new());
        }
        let query = RecordQuery {
            conversations: Some(conversations),
            ..query.clone()
        };
        self.keeper.search(&query)
    }

    fn restore(&self, _: &str, _: Restore) -> Result<Restored, Fault> {
        Err(denied().into())
    }

    fn sweep(&self, _: usize, _: Option<&str>, _: bool) -> Result<SweepPage, Fault> {
        Err(denied().into())
    }
}
