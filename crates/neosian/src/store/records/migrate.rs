//! Whole turns turned into records in place (DESIGN §40.54 item 21,
//! T1b2c3): `neosian records migrate`, one conversation at a time under one
//! hold of the root lock. The new log and each migrated turn's records file
//! are staged in `.migrate-preparing` and made durable; renaming that
//! directory to `.migrate-pending` is the intent. The records files then
//! move into `records/`, the log replaces `turns.jsonl`, and the directory
//! is retired and removed. Every operation on the conversation first
//! removes a preparation that never published and rolls a pending one
//! forward, so no reader meets a header before its records or half a log.
//! A turn is migrated only when its records give its stored line back byte
//! for byte (`stage`); every other row keeps its bytes and place.

mod stage;

use std::fs;
use std::io;
use std::path::Path;

use serde::Serialize;

#[cfg(test)]
pub(super) use self::stage::migration_id;
use super::RECORD_FILES;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rows::{CONVERSATIONS, TURNS};
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::fileio::{private_mkdir, remove_tree, within};

const PREPARE: &str = ".migrate-preparing";
const PENDING: &str = ".migrate-pending";
const RETIRED: &str = ".migrate-retired";

/// Whole turns a migration left as they were, by reason.
#[derive(Debug, Default, PartialEq, Eq, Serialize)]
pub struct Kept {
    /// The line spells its turn otherwise than this release writes it.
    pub spelling: u64,
    /// The line holds what a turn or its records cannot carry.
    pub content: u64,
    /// The turn is over `NEOSIAN_MAX_RECORD_TURN_BYTES`.
    pub size: u64,
    /// The turn's number already has a records file.
    pub number: u64,
}

/// What one conversation's migration did and what it found.
#[derive(Debug, Default, PartialEq, Eq, Serialize)]
pub struct Migrated {
    pub conversation_id: String,
    /// Whole turns this run turned into records.
    pub migrated: u64,
    /// Turns that were records already.
    pub records: u64,
    /// Erased turns, which hold nothing to migrate.
    pub skeletons: u64,
    pub kept: Kept,
}

#[cfg(test)]
thread_local! {
    /// The step after which a test's migration stops, as a killed process would.
    pub(super) static STOP: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
}

fn step(_done: u8) -> Result<(), Error> {
    #[cfg(test)]
    if STOP.with(std::cell::Cell::get) == _done {
        return Err(Error::Value(format!("stopped after migrate step {_done}")));
    }
    Ok(())
}

fn unreadable(id: &str) -> Error {
    Refused::format_unsupported(id, "unreadable migration staging").into()
}

impl FileStore {
    /// Every conversation directory by its exact name, sorted, in one pass
    /// of their parent; none is settled or otherwise touched. A selection
    /// is checked against these names: a case-folding file system would
    /// answer for another spelling, and a listing that settled every
    /// conversation would finish work the selection never named.
    pub fn stored_conversations(&self) -> Result<Vec<String>, Error> {
        let _held = self.lock()?;
        self.stored_names()
    }

    pub(super) fn stored_names(&self) -> Result<Vec<String>, Error> {
        let parent = self.root.join(CONVERSATIONS);
        if !within(&self.root, &parent)? {
            let why = MemoryError::path_invalid("conversations", "escapes the store root");
            return Err(why.into());
        }
        let entries = match fs::read_dir(&parent) {
            Ok(entries) => entries,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => return Err(error.into()),
        };
        let mut names = Vec::new();
        for entry in entries {
            let entry = entry?;
            let name = entry.file_name().into_string();
            if let Ok(name) = name.as_deref()
                && entry.file_type()?.is_dir()
                && parse_conversation_id(name).is_ok()
            {
                names.push(name.to_string());
            }
        }
        names.sort();
        Ok(names)
    }

    /// Turn every whole turn of one conversation that its records give back
    /// byte for byte into records; a conversation without a log is nothing.
    /// The name is one read from `stored_conversations` or `conversations`,
    /// so it is exact, and a run over a store reads its directory once.
    pub fn migrate_records(
        &self,
        conversation_id: &str,
        limits: &limits::Records,
    ) -> Result<Migrated, Error> {
        let _held = self.lock()?;
        self.conversation_dir(conversation_id)?;
        if !self.records_enabled()? {
            let why = "records are not enabled on this store; run `neosian records enable`";
            return Err(MemoryError::configuration(why).into());
        }
        let mut found = Migrated {
            conversation_id: conversation_id.to_string(),
            ..Migrated::default()
        };
        self.settle(conversation_id)?;
        let log = self.conversation_file(conversation_id, TURNS)?;
        if !log.is_file() {
            return Ok(found);
        }
        let preparing = self.conversation_file(conversation_id, PREPARE)?;
        private_mkdir(&preparing.join(RECORD_FILES))?;
        let result = (|| {
            let bound = limits.max_record_turn_bytes;
            stage::stage(self, conversation_id, &log, &preparing, bound, &mut found)?;
            if found.migrated == 0 {
                return Ok(());
            }
            self.sync(&preparing.join(RECORD_FILES).join("entry"))?;
            step(1)?;
            let pending = self.conversation_file(conversation_id, PENDING)?;
            fs::rename(&preparing, &pending)?;
            self.sync(&pending)?;
            step(2)?;
            self.finish_migration(conversation_id, &pending)
        })();
        remove_tree(&preparing)?;
        result.map(|()| found)
    }

    /// Remove a migration that never published and roll a published one
    /// forward; a published one on a store without records is refused by name.
    pub(super) fn settle_migration(&self, id: &str) -> Result<(), Error> {
        let directory = self.conversation_dir(id)?;
        for name in [PREPARE, RETIRED] {
            if directory.join(name).try_exists()? {
                remove_tree(&self.conversation_file(id, name)?)?;
            }
        }
        if !directory.join(PENDING).try_exists()? {
            return Ok(());
        }
        let pending = self.conversation_file(id, PENDING)?;
        if !self.records_enabled()? {
            let why = "a migration marker requires records to be enabled";
            return Err(Refused::format_unsupported(id, why).into());
        }
        self.finish_migration(id, &pending)
    }

    /// Publish a pending migration; every step is safe to repeat. The log
    /// still staged means the swap has not happened: whatever records files
    /// are left move first, so a header never precedes its records.
    fn finish_migration(&self, id: &str, pending: &Path) -> Result<(), Error> {
        let log = self.conversation_file(id, &format!("{PENDING}/{TURNS}"))?;
        if log.try_exists()? {
            let staged = self.conversation_file(id, &format!("{PENDING}/{RECORD_FILES}"))?;
            let records = self.conversation_file(id, RECORD_FILES)?;
            private_mkdir(&records)?;
            for entry in fs::read_dir(&staged)? {
                let name = entry?.file_name();
                let turn = name.to_str().and_then(|name| name.strip_suffix(".jsonl"));
                let Some(turn) = turn.and_then(|turn| turn.parse::<u64>().ok()) else {
                    return Err(unreadable(id));
                };
                fs::rename(staged.join(&name), self.record_file(id, turn)?)?;
                step(3)?;
            }
            self.sync_existing(&records)?;
            step(4)?;
            let live = self.conversation_file(id, TURNS)?;
            fs::rename(&log, &live)?;
            self.sync(&live)?;
            step(5)?;
        }
        let retired = self.conversation_file(id, RETIRED)?;
        fs::rename(pending, &retired)?;
        self.sync(&retired)?;
        step(6)?;
        remove_tree(&retired)?;
        self.sync(&retired)?;
        Ok(())
    }
}
