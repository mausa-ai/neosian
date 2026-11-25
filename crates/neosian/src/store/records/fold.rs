//! Turns stored as records turned back into whole turns (DESIGN §40.54
//! item 21, T1b2c4): `neosian records fold`, one conversation at a time
//! under one hold of the root lock, the rollback of a store that keeps
//! records. Open drafts and staged restores are pending work: they refuse
//! the conversation, or go with `--discard-pending`, but only once the
//! whole conversation has been staged, so a refusal consumes none of it,
//! and under this journal alone, kept in the conversation's own directory. The new log, a digest
//! receipt for each folded turn a client's seal made (`receipt`), the list
//! of records files made needless and the pending work to discard are
//! staged in `.fold-preparing` and made durable; the rename to
//! `.fold-pending` is the intent. The log is published first, then the
//! receipts, and only then are the records files removed, so no turn is
//! ever without its messages and no seal without its evidence. Every
//! operation on the conversation first removes a preparation that never
//! published and rolls a pending one forward.

mod places;
pub(in crate::store) mod receipt;
mod stage;

pub use self::places::Listing;
#[cfg(test)]
pub use self::places::Places;

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::Path;

use super::RECORD_FILES;
use super::sweep::Selection;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rows::TURNS;
use crate::conversation::turn_records::migration::Folded;
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::fileio::{atomic_write, private_mkdir, remove_file, remove_tree};

const PREPARE: &str = ".fold-preparing";
const PENDING: &str = ".fold-pending";
const RETIRED: &str = ".fold-retired";
const FILES: &str = "files";
const RECEIPTS: &str = "receipts";
const DISCARD: &str = "discard";

#[cfg(test)]
thread_local! {
    /// The step after which a test's fold stops, as a killed process would.
    pub(super) static STOP: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
}

fn step(_done: u8) -> Result<(), Error> {
    #[cfg(test)]
    if STOP.with(std::cell::Cell::get) == _done {
        return Err(Error::Value(format!("stopped after fold step {_done}")));
    }
    Ok(())
}

fn unreadable(id: &str) -> Error {
    Refused::format_unsupported(id, "unreadable fold staging").into()
}

impl FileStore {
    /// Turn every turn of one conversation stored as records back into a
    /// whole turn. `listing` is the run's `record_places`, brought up to
    /// date under the lock when a directory has changed since. Pending
    /// work refuses the conversation unless `discard_pending`; a turn over
    /// `NEOSIAN_MAX_TURN_BYTES` refuses it before that turn is assembled.
    /// A refusal leaves the conversation and its pending work as they were.
    pub fn fold_records(
        &self,
        conversation_id: &str,
        listing: &mut Listing,
        discard_pending: bool,
        limits: &limits::Records,
    ) -> Result<Folded, Error> {
        let _held = self.lock()?;
        parse_conversation_id(conversation_id)?;
        if !self.records_enabled()? {
            let why = "records are not enabled on this store; run `neosian records enable`";
            return Err(MemoryError::configuration(why).into());
        }
        // On a file system that folds case another conversation answers
        // for this name's paths: only what is stored under exactly this
        // name is settled, read or written.
        let places = self.places_now(conversation_id, listing)?;
        if places.staged {
            self.settle_sweep(conversation_id)?;
        }
        if places.stored {
            self.settle_stored(conversation_id)?;
        }
        let mut found = Folded {
            conversation_id: conversation_id.to_string(),
            ..Folded::default()
        };
        let pending = self.pending_work(conversation_id, places.stored, places.staged)?;
        let (drafts, restores) = (pending.drafts.len(), pending.restores.len());
        if drafts + restores > 0 && !discard_pending {
            return Err(Error::Value(format!(
                "conversation {conversation_id} holds pending work ({drafts} open drafts, \
                 {restores} staged restores); let it finish or pass --discard-pending to \
                 remove it; nothing in this conversation was folded"
            )));
        }
        (found.drafts, found.restores) = (drafts as u64, restores as u64);
        if !places.stored {
            // A staged restore alone. Its own directory is all that bears
            // its name, and the sweep's journal is kept there.
            self.discard_pending(conversation_id, &pending)?;
            return Ok(found);
        }
        // The journal is the conversation's own directory, pending work
        // included: no other name's is written, whatever its spelling.
        let log = self.conversation_file(conversation_id, TURNS)?;
        let preparing = self.conversation_file(conversation_id, PREPARE)?;
        private_mkdir(&preparing.join(RECEIPTS))?;
        let result = (|| {
            if log.is_file() {
                let bound = limits.max_turn_bytes;
                stage::stage(self, conversation_id, &log, &preparing, bound, &mut found)?;
            } else {
                atomic_write(&preparing.join(FILES), "", true)?;
            }
            if found.folded == 0 && drafts + restores == 0 {
                return Ok(());
            }
            let discard = serde_json::to_string(&pending).map_err(io::Error::other)?;
            atomic_write(&preparing.join(DISCARD), &discard, true)?;
            self.sync(&preparing.join(RECEIPTS).join("entry"))?;
            step(1)?;
            let pending = self.conversation_file(conversation_id, PENDING)?;
            fs::rename(&preparing, &pending)?;
            self.sync(&pending)?;
            step(2)?;
            self.finish_fold(conversation_id, &pending)
        })();
        remove_tree(&preparing)?;
        result.map(|()| found)
    }

    /// Remove a fold that never published and roll a published one
    /// forward. Rolling forward needs no records marker: it publishes whole
    /// rows and removes files, which only makes the store readable to more
    /// binaries.
    pub(super) fn settle_fold(&self, id: &str) -> Result<(), Error> {
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
        // The reader that found this journal may use another spelling on a
        // case-insensitive filesystem. Its discard still belongs to the
        // exact stored conversation that published it.
        let names = self.stored_names()?;
        let owner = names
            .iter()
            .find(|name| name.as_str() == id)
            .or_else(|| names.iter().find(|name| name.eq_ignore_ascii_case(id)))
            .ok_or_else(|| unreadable(id))?;
        self.finish_fold(owner, &pending)
    }

    /// Publish a pending fold; every step is safe to repeat. The log goes
    /// first: until it is in place every header still has its records.
    fn finish_fold(&self, id: &str, pending: &Path) -> Result<(), Error> {
        let log = self.conversation_file(id, &format!("{PENDING}/{TURNS}"))?;
        if log.try_exists()? {
            let live = self.conversation_file(id, TURNS)?;
            fs::rename(&log, &live)?;
            self.sync(&live)?;
        }
        step(3)?;
        let staged = self.conversation_file(id, &format!("{PENDING}/{RECEIPTS}"))?;
        for entry in fs::read_dir(&staged).map_err(|_| unreadable(id))? {
            let name = entry?.file_name();
            let turn = name.to_str().and_then(|name| name.parse::<u64>().ok());
            let target = self.folded_file(id, turn.ok_or_else(|| unreadable(id))?)?;
            // A turn folded before keeps the receipt of its first fold.
            if !target.try_exists()? {
                private_mkdir(&self.folded_dir(id)?)?;
                fs::rename(staged.join(&name), &target)?;
            }
        }
        self.sync_existing(&self.folded_dir(id)?)?;
        step(4)?;
        let files = self.conversation_file(id, &format!("{PENDING}/{FILES}"))?;
        let list = fs::File::open(&files).map_err(|_| unreadable(id))?;
        for line in BufReader::new(list).lines() {
            let turn = line?.parse::<u64>().map_err(|_| unreadable(id))?;
            remove_file(&self.record_file(id, turn)?)?;
            step(5)?;
        }
        let records = self.conversation_file(id, RECORD_FILES)?;
        // Gone when the fold emptied it; a file no header owned keeps it.
        let _ = fs::remove_dir(&records);
        self.sync_existing(&records)?;
        let discard = self.conversation_file(id, &format!("{PENDING}/{DISCARD}"))?;
        let discard = fs::read(&discard).map_err(|_| unreadable(id))?;
        let discard: Selection = serde_json::from_slice(&discard).map_err(|_| unreadable(id))?;
        // Its restores go only while their directory still bears this name.
        let staged = !discard.restores.is_empty() && self.staged_exactly(id)?;
        self.discard_named(id, &discard, staged)?;
        step(6)?;
        let retired = self.conversation_file(id, RETIRED)?;
        fs::rename(pending, &retired)?;
        self.sync(&retired)?;
        step(7)?;
        remove_tree(&retired)?;
        self.sync(&retired)?;
        Ok(())
    }
}
