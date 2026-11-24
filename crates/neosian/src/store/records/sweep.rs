//! The operator's stale-draft and restore sweep (§40.54 item 13). Select
//! at most one page and a lookahead across both directory trees under the
//! store lock. Each conversation's discard has its own durable intent.
use std::collections::BTreeMap;
use std::{fs, io};

use serde::{Deserialize, Serialize};
use serde_json::json;

use super::{drafts, restore_stale, staging, stale};
use crate::conversation::ids::check_conversation_id;
use crate::conversation::rows::CONVERSATIONS;
use crate::conversation::search::visit_ids;
use crate::conversation::turn_records::{check_draft_id, check_id};
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::store::FileStore;
use crate::store::cursor::{self, Kind};
use crate::store::error::MemoryError;
use crate::store::fileio::atomic_write;
use crate::store::time::Stamp;

const METHOD: &str = "sweep-v1";
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

#[derive(Debug, Serialize)]
pub struct SweepConversation {
    pub conversation_id: String,
    pub drafts: usize,
    pub restores: usize,
    pub freed_bytes: u64,
}

#[derive(Debug, Serialize)]
pub struct SweepPage {
    pub conversations: Vec<SweepConversation>,
    pub next_cursor: Option<String>,
    pub write: bool,
}

/// Persist only the names selected while the lock was held. A draft's
/// state or a restore's directory can already be gone on a replay.
#[derive(Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    drafts: Vec<String>,
    restores: Vec<String>,
}

fn after(cursor: &str) -> Result<String, Error> {
    if !(1..=4096).contains(&cursor.len()) {
        return Err(Error::Value("sweep cursor must be 1 to 4096 bytes".into()));
    }
    let parts = cursor::decode(METHOD, cursor, &[Kind::Str]).map_err(Error::Value)?;
    let id = parts[0].as_str().expect("decoded string");
    check_conversation_id(id).map_err(Error::Value)?;
    Ok(id.to_owned())
}

impl FileStore {
    pub fn sweep_records(
        &self,
        limit: usize,
        cursor: Option<&str>,
        write: bool,
    ) -> Result<SweepPage, Error> {
        if !(1..=100).contains(&limit) {
            return Err(Error::Value("sweep limit must be in 1..100".into()));
        }
        let after = cursor.map(after).transpose()?;
        let seconds = limits::Records::load()?.draft_seconds;
        let _held = self.lock()?;
        if !self.records_enabled()? {
            return Err(MemoryError::configuration(
                "records are not enabled on this store; run `neosian records enable`",
            )
            .into());
        }
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
        let next_cursor = more.then(|| {
            cursor::encode(
                METHOD,
                &[json!(page.last_key_value().expect("positive page limit").0)],
            )
        });
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
