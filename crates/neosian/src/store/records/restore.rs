//! `restore_records` on a FileStore up to the commit (DESIGN §40.54 item
//! 12, T1b2b1): a probe, a discard, an opening onto an empty target, parts
//! continuing the stream. A part's items are placed against a copy of the
//! stream and repeats compared with what is staged, and only when every
//! item has passed are stale restores discarded and the new lines written,
//! the files first and the state last, so a refused request changes
//! nothing. Memory follows the part: its items, its new lines, the staged
//! lines a repeat is compared with one at a time.

use serde_json::json;

use super::restore_lines::new_line;
use super::restore_stale;
use super::staging::{self, RestoreState, Staging};
use crate::conversation::rows::{PROJECTIONS, TURNS};
use crate::conversation::turn_records::{
    Placed, Position, Restore, Restored, Sizes, Staged, StreamRefusal, conflict, size_refusal,
};
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::fileio::private_mkdir;
use crate::store::time::Stamp;

fn answer(restore: &str, state: Staged, position: Option<Position>) -> Restored {
    Restored {
        restore: restore.to_string(),
        state,
        position,
        stored_bytes: 0,
        freed_bytes: 0,
        digest: None,
    }
}

fn reason(conversation_id: &str, reason: &str, position: Position) -> Error {
    conflict(
        conversation_id,
        reason,
        json!({"position": position.to_json()}),
    )
    .into()
}

impl FileStore {
    /// One part of a restore; the actor of every item is stored as sent.
    pub fn restore_records(
        &self,
        conversation_id: &str,
        restore: Restore,
        limits: &limits::Records,
    ) -> Result<Restored, Error> {
        restore.check(conversation_id).map_err(Error::Value)?;
        let _held = self.lock()?;
        let directory = self.conversation_dir(conversation_id)?;
        if !self.records_enabled()? {
            let why = "records are not enabled on this store; run `neosian records enable`";
            return Err(MemoryError::configuration(why).into());
        }
        self.settle(conversation_id)?;
        let staging = self.staging(conversation_id, &restore.restore)?;
        self.sweep(&staging.dir)?;
        let id = restore.restore.as_str();
        let loaded = match staging::load(self, &staging, conversation_id) {
            // A discard removes even a restore whose state cannot be read.
            Err(_) if restore.discard && staging.dir.is_dir() => {
                let bytes = restore_stale::recount(&staging, &Sizes::new(conversation_id, id));
                restore_stale::remove(self, &staging.dir)?;
                let mut gone = answer(id, Staged::Discarded, None);
                gone.freed_bytes = bytes;
                return Ok(gone);
            }
            loaded => loaded?,
        };
        let (state, opening) = match loaded {
            None if restore.discard || restore.is_probe() => {
                return Ok(answer(id, Staged::None, None));
            }
            Some(state) if restore.discard => {
                restore_stale::remove(self, &staging.dir)?;
                let mut gone = answer(id, Staged::Discarded, Some(state.stream.position()));
                gone.freed_bytes = state.bytes;
                return Ok(gone);
            }
            Some(state) if restore.is_probe() => {
                return Ok(answer(id, Staged::Open, Some(state.stream.position())));
            }
            _ if restore.commit => {
                return Err(Error::Value(
                    "a staged restore cannot be committed yet".into(),
                ));
            }
            None => {
                if directory.join(TURNS).is_file() || directory.join(PROJECTIONS).is_file() {
                    return Err(Refused::conflict(conversation_id, "target_occupied").into());
                }
                let now = Stamp::now().instant();
                let (stale, open) =
                    restore_stale::others(self, conversation_id, id, limits.draft_seconds, now)?;
                if let Some(open) = open {
                    let details = json!({"restore": open});
                    return Err(conflict(conversation_id, "restore_open", details).into());
                }
                let bookkeeping = Sizes::new(conversation_id, id).bookkeeping();
                (RestoreState::new(0), Some((stale, bookkeeping)))
            }
            Some(state) => (state, None),
        };
        self.part(conversation_id, &restore, &staging, state, opening, limits)
    }

    /// Place, compare and then write one part's items.
    fn part(
        &self,
        conversation_id: &str,
        restore: &Restore,
        staging: &Staging,
        mut state: RestoreState,
        opening: Option<(Vec<(std::path::PathBuf, u64)>, u64)>,
        limits: &limits::Records,
    ) -> Result<Restored, Error> {
        let id = restore.restore.as_str();
        let sizes = Sizes::new(conversation_id, id);
        let before = state.stream.position();
        let mut stream = state.stream.clone();
        let (mut lines, mut repeats, mut stored) = (Vec::new(), Vec::new(), 0_u64);
        for item in restore.items() {
            match stream.admit(&item, limits.max_record_turn_bytes) {
                Ok(Placed::Repeat) => repeats.push(item),
                Ok(Placed::New) => {
                    let (line, bytes) = new_line(&item, &sizes, id, conversation_id);
                    lines.push(line);
                    stored += bytes;
                }
                Err(StreamRefusal::Gap) => {
                    return Err(reason(conversation_id, "restore_gap", before));
                }
                Err(StreamRefusal::Invalid(why)) => return Err(Error::Value(why)),
                Err(StreamRefusal::Bound { bytes, maximum }) => {
                    return Err(Error::Value(size_refusal(bytes, maximum)));
                }
            }
        }
        if !self.repeats_match(conversation_id, staging, id, &repeats)? {
            return Err(reason(conversation_id, "restore_mismatch", before));
        }
        let mut freed = 0;
        if let Some((stale, bookkeeping)) = opening {
            for (dir, bytes) in stale {
                restore_stale::remove(self, &dir)?;
                freed += bytes;
            }
            private_mkdir(&staging.records)?;
            stored += bookkeeping;
        }
        self.write_lines(conversation_id, staging, &mut state, lines)?;
        state.stream = stream;
        state.bytes += stored;
        state.touched_at = Stamp::now().z();
        staging::save(staging, &state)?;
        self.sync(&staging.state)?;
        let mut done = answer(id, Staged::Open, Some(state.stream.position()));
        (done.stored_bytes, done.freed_bytes) = (stored, freed);
        Ok(done)
    }
}
