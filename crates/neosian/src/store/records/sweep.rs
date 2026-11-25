//! The operator's stale-draft and restore sweep (§40.54 item 13). Select
//! at most one page and a lookahead across both directory trees under the
//! store lock. Each conversation's discard has its own durable intent.
use std::collections::BTreeMap;
use std::{fs, io};

use serde::{Deserialize, Serialize};

use super::{drafts, restore_stale, staging, stale};
use crate::conversation::rows::CONVERSATIONS;
use crate::conversation::search::visit_ids;
use crate::conversation::turn_records::sweep::{
    SweepConversation, SweepPage, cursor_after, start_after,
};
use crate::conversation::turn_records::{check_draft_id, check_id};
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::fileio::atomic_write;
use crate::store::time::Stamp;

const PENDING: &str = ".sweeping";

#[cfg(test)]
thread_local! {
    pub(super) static STOP: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
}

fn step(_n: u8) -> Result<(), Error> {
    #[cfg(test)]
    if STOP.with(std::cell::Cell::get) == _n {
        return Err(Error::Value(format!("stopped after sweep step {_n}")));
    }
    Ok(())
}

/// Persist only the names selected while the lock was held. A draft's
/// state or a restore's directory can already be gone on a replay.
#[derive(Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Selection {
    pub(super) drafts: Vec<String>,
    pub(super) restores: Vec<String>,
}

impl FileStore {
    pub fn sweep_records(
        &self,
        limit: usize,
        cursor: Option<&str>,
        write: bool,
    ) -> Result<SweepPage, Error> {
        let after = start_after(limit, cursor)?;
        let _held = self.lock()?;
        if !self.records_enabled()? {
            return Err(MemoryError::configuration(
                "records are not enabled on this store; run `neosian records enable`",
            )
            .into());
        }
        let seconds = limits::Records::load()?.draft_seconds;
        let now = Stamp::now().instant();
        let mut page: BTreeMap<String, (Selection, u64)> = BTreeMap::new();
        for tree in [CONVERSATIONS, staging::RESTORES] {
            let root = self.contained(self.root.join(tree), tree)?;
            visit_ids(&root, |id| {
                if after.as_deref().is_some_and(|last| id <= last)
                    || (page.len() > limit
                        && page
                            .keys()
                            .next_back()
                            .is_some_and(|last| id > last.as_str()))
                {
                    return Ok(());
                }
                // Use the spelling in each directory tree. A case-folding
                // volume can have a draft under Mixed and a restore under mixed.
                self.settle(id)?;
                let stale = if tree == CONVERSATIONS {
                    stale::stale(&self.root, &self.drafts_dir(id)?, seconds, now, id)?
                } else {
                    restore_stale::others(self, id, "", seconds, now)?.0
                };
                if !stale.is_empty() {
                    let (selection, freed) = page.entry(id.to_owned()).or_default();
                    let names = if tree == CONVERSATIONS {
                        &mut selection.drafts
                    } else {
                        &mut selection.restores
                    };
                    for (name, bytes) in stale {
                        *freed = freed.checked_add(bytes).ok_or_else(|| {
                            Refused::format_unsupported(id, "stale item byte counts overflow")
                        })?;
                        names.push(name);
                    }
                    if page.len() > limit + 1 {
                        page.pop_last();
                    }
                }
                Ok(())
            })?;
        }
        let more = page.len() > limit;
        if more {
            page.pop_last();
        }
        let next_cursor =
            more.then(|| cursor_after(page.last_key_value().expect("positive page limit").0));
        let mut conversations = Vec::with_capacity(page.len());
        for (conversation_id, (selection, freed_bytes)) in page {
            if write {
                let marker = self.restores_dir(&conversation_id)?.join(PENDING);
                atomic_write(
                    &marker,
                    &serde_json::to_string(&selection).map_err(io::Error::other)?,
                    true,
                )?;
                self.sync(&marker)?;
                step(1)?;
                self.discard_selection(&conversation_id, &selection)?;
            }
            conversations.push(SweepConversation {
                conversation_id,
                drafts: selection.drafts.len(),
                restores: selection.restores.len(),
                freed_bytes,
            });
        }
        Ok(SweepPage {
            conversations,
            next_cursor,
            write,
        })
    }

    pub(super) fn settle_sweep(&self, id: &str) -> Result<(), Error> {
        // A restore-only sweep must not materialize a live conversation.
        // Its marker also keeps the staging parent discoverable until replay ends.
        crate::conversation::ids::parse_conversation_id(id)?;
        let marker = self.root.join(staging::RESTORES).join(id).join(PENDING);
        if !marker.try_exists()? {
            return Ok(());
        }
        let marker = self.contained(marker, id)?;
        if !self.records_enabled()? {
            return Err(Refused::format_unsupported(
                id,
                "a sweep marker requires records to be enabled",
            )
            .into());
        }
        let selection: Selection = serde_json::from_slice(&fs::read(marker)?)
            .map_err(|_| Refused::format_unsupported(id, "unreadable sweep marker"))?;
        for draft in &selection.drafts {
            check_draft_id(draft).map_err(Error::Value)?;
        }
        for restore in &selection.restores {
            check_id("restore", restore).map_err(Error::Value)?;
        }
        self.discard_selection(id, &selection)
    }

    /// Every open draft and every staged restore of a conversation,
    /// whatever its age: what a fold calls pending work. `stored` and
    /// `staged` say which of the two trees holds the name exactly.
    pub(super) fn pending_work(
        &self,
        id: &str,
        stored: bool,
        staged: bool,
    ) -> Result<Selection, Error> {
        let mut pending = Selection::default();
        if stored {
            let drafts = stale::stale(&self.root, &self.drafts_dir(id)?, 0, i64::MAX, id)?;
            pending.drafts = drafts.into_iter().map(|(name, _)| name).collect();
        }
        if staged {
            let restores = restore_stale::others(self, id, "", 0, i64::MAX)?.0;
            pending.restores = restores.into_iter().map(|(name, _)| name).collect();
        }
        Ok(pending)
    }

    /// Discard a selection under its own durable intent, as a sweep does.
    pub(super) fn discard_pending(&self, id: &str, selection: &Selection) -> Result<(), Error> {
        if selection.drafts.is_empty() && selection.restores.is_empty() {
            return Ok(());
        }
        let marker = self.restores_dir(id)?.join(PENDING);
        let text = serde_json::to_string(selection).map_err(io::Error::other)?;
        atomic_write(&marker, &text, true)?;
        self.sync(&marker)?;
        self.discard_selection(id, selection)
    }

    /// Remove a selection a fold published in its own journal. No marker
    /// of the sweep's is written: the drafts go from this conversation's
    /// own directory, the restores only when `staged` says their directory
    /// bears exactly this name.
    pub(super) fn discard_named(
        &self,
        id: &str,
        selection: &Selection,
        staged: bool,
    ) -> Result<(), Error> {
        let drafts = self.drafts_dir(id)?;
        for name in &selection.drafts {
            check_draft_id(name).map_err(Error::Value)?;
            drafts::remove(&drafts::Paths::entries(&drafts, name))?;
        }
        self.sync_existing(&drafts)?;
        for name in selection.restores.iter().filter(|_| staged) {
            check_id("restore", name).map_err(Error::Value)?;
            restore_stale::remove(self, &self.restores_dir(id)?.join(name))?;
        }
        Ok(())
    }

    fn discard_selection(&self, id: &str, selection: &Selection) -> Result<(), Error> {
        let drafts = self.drafts_dir(id)?;
        for name in &selection.drafts {
            drafts::remove(&drafts::Paths::entries(&drafts, name))?;
        }
        self.sync_existing(&drafts)?;
        step(2)?;
        for name in &selection.restores {
            let path = self.restores_dir(id)?.join(name);
            restore_stale::remove(self, &path)?;
        }
        step(3)?;
        let parent = self.restores_dir(id)?;
        let marker = parent.join(PENDING);
        fs::remove_file(&marker)?;
        restore_stale::clean_parent(self, &parent)?;
        Ok(())
    }
}
