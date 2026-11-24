//! `write_records` on a FileStore (DESIGN §40.54 item 9): a probe, a
//! discard, a part opening or continuing a draft, a seal. A request's new
//! records are checked against a copy of the draft's check, and only when
//! every check has passed, the store's bound at the seal included, are
//! stale drafts discarded and the records written, so a refused request
//! changes nothing (the review of 2481787, findings 5 and 6); repeats are
//! compared by digest. Memory follows the part: the request's records, the
//! draft's small state, and for repeats one stored line at a time.

use serde_json::json;

use super::drafts::{self, DraftState, Paths};
use super::stale;
use crate::conversation::Error;
use crate::conversation::rows::{PROJECTIONS, TURNS};
use crate::conversation::rows_header::RecordHeader;
use crate::conversation::turn_records::{
    Header, Refusal, Sizes, State, Write, Written, conflict, size_refusal,
};
use crate::limits;
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::fileio::append_line;
use crate::store::time::Stamp;

/// The most drafts one conversation holds open.
pub const OPEN_DRAFTS: usize = 16;

fn header(sealed: &RecordHeader) -> Header {
    Header {
        turn: sealed.turn,
        created_at: sealed.created_at,
        actor: sealed.actor.clone(),
        redacted: false,
        records: sealed.records,
        bytes: sealed.bytes,
    }
}

fn written(draft: &str, state: State, next_seq: u64) -> Written {
    Written {
        draft: draft.to_string(),
        state,
        next_seq,
        turn: None,
        stored_bytes: 0,
        freed_bytes: 0,
    }
}

/// What an opening request removes once its checks have passed.
struct Opening {
    stale: Vec<(String, u64)>,
}

impl FileStore {
    /// One part of a draft; the actor is already stamped by the caller.
    pub fn write_records(
        &self,
        conversation_id: &str,
        write: Write,
        limits: &limits::Records,
    ) -> Result<Written, Error> {
        write.check().map_err(Error::Value)?;
        let _held = self.lock()?;
        let directory = self.conversation_dir(conversation_id)?;
        if !self.records_enabled()? {
            let why = "records are not enabled on this store; run `neosian records enable`";
            return Err(MemoryError::configuration(why).into());
        }
        self.settle(conversation_id)?;
        let drafts = self.drafts_dir(conversation_id)?;
        self.sweep(&drafts)?;
        let paths = Paths::new(&self.root, &drafts, &write.draft, conversation_id)?;
        if let Some(sealed) = drafts::sealed(&paths, conversation_id)? {
            return self.after_seal(conversation_id, &sealed, &write);
        }
        let sizes = Sizes::new(conversation_id, &write.draft);
        let (mut state, mut stored, opening) = match drafts::load(&paths, conversation_id)? {
            Some(state) if write.discard => {
                drafts::remove(&paths)?;
                let mut answer = written(&write.draft, State::Discarded, state.check.next_seq());
                answer.freed_bytes = state.bytes;
                return Ok(answer);
            }
            None if write.discard || (write.records.is_empty() && !write.seal) => {
                return Ok(written(&write.draft, State::None, 0));
            }
            None if write.records.is_empty() => {
                return Err(Error::Value(
                    "a seal needs at least one record in the draft".into(),
                ));
            }
            None => {
                let now = Stamp::now().instant();
                let old = stale::stale(
                    &self.root,
                    &drafts,
                    limits.draft_seconds,
                    now,
                    conversation_id,
                )?;
                if stale::open_count(&drafts)?.saturating_sub(old.len()) >= OPEN_DRAFTS {
                    let details = json!({"limit": OPEN_DRAFTS});
                    return Err(conflict(conversation_id, "drafts_open", details).into());
                }
                if let Some(stated) = write
                    .turn_bytes
                    .filter(|b| *b > limits.max_record_turn_bytes)
                {
                    return Err(Error::Value(size_refusal(
                        stated,
                        limits.max_record_turn_bytes,
                    )));
                }
                let state =
                    DraftState::new(write.actor.clone(), write.turn_bytes, sizes.bookkeeping());
                (state, sizes.bookkeeping(), Some(Opening { stale: old }))
            }
            Some(state) => {
                if write
                    .actor
                    .as_ref()
                    .is_some_and(|actor| Some(actor) != state.actor.as_ref())
                {
                    let details = json!({"draft": write.draft});
                    return Err(conflict(conversation_id, "draft_actor", details).into());
                }
                if write.turn_bytes.is_some() && write.turn_bytes != state.turn_bytes {
                    return Err(self.draft_size(
                        conversation_id,
                        write.turn_bytes,
                        state.check.bytes(),
                    ));
                }
                (state, 0, None)
            }
        };
        let next = state.check.next_seq();
        let first = write.records.first().map_or(next, |record| record.seq);
        if first > next {
            return Err(conflict(conversation_id, "draft_gap", json!({"next_seq": next})).into());
        }
        let repeats = write
            .records
            .iter()
            .take_while(|record| record.seq < next)
            .count();
        let stored_digests = match repeats {
            0 => Vec::new(),
            _ => drafts::digests(
                std::fs::File::open(&paths.records)?,
                first,
                repeats,
                conversation_id,
            )?,
        };
        for (record, digest) in write.records.iter().zip(&stored_digests) {
            if drafts::hex(&record.digest(None)) != *digest {
                let details = json!({"next_seq": next, "seq": record.seq});
                return Err(conflict(conversation_id, "draft_mismatch", details).into());
            }
        }
        let fresh = &write.records[repeats..];
        let mut check = state.check.clone();
        for record in fresh {
            check
                .admit(record, state.turn_bytes, limits.max_record_turn_bytes)
                .map_err(|refusal| match refusal {
                    Refusal::Invalid(why) => Error::Value(why),
                    Refusal::Stated { stated, bytes } => {
                        self.draft_size(conversation_id, Some(stated), bytes)
                    }
                    Refusal::Bound { bytes, maximum } => Error::Value(size_refusal(bytes, maximum)),
                })?;
        }
        let seal_turn = if write.seal {
            check.sealable().map_err(Error::Value)?;
            if check.bytes() > limits.max_record_turn_bytes {
                let why = size_refusal(check.bytes(), limits.max_record_turn_bytes);
                return Err(Error::Value(why));
            }
            if state
                .turn_bytes
                .is_some_and(|stated| stated != check.bytes())
            {
                return Err(self.draft_size(conversation_id, state.turn_bytes, check.bytes()));
            }
            Some(self.next_number(&directory.join(TURNS), conversation_id)?)
        } else {
            None
        };
        let mut freed = 0;
        if let Some(opening) = opening {
            for (stale, bytes) in &opening.stale {
                drafts::remove(&Paths::entries(&drafts, stale))?;
                freed += bytes;
            }
            drafts::remove(&paths)?;
        }
        if !fresh.is_empty() {
            let text: String = fresh.iter().map(drafts::line).collect();
            append_line(&paths.records, &text, true)?;
            state.file_bytes += text.len() as u64;
            let added: u64 = fresh.iter().map(|record| sizes.record(record)).sum();
            stored += added;
            state.bytes += added;
        }
        state.check = check;
        let Some(turn) = seal_turn else {
            state.touched_at = Stamp::now().z();
            drafts::save(&paths, &state)?;
            let mut answer = written(&write.draft, State::Open, state.check.next_seq());
            (answer.stored_bytes, answer.freed_bytes) = (stored, freed);
            return Ok(answer);
        };
        let creates = !directory.join(TURNS).is_file() && !directory.join(PROJECTIONS).is_file();
        let sealed = self.seal(conversation_id, &write.draft, &state, turn)?;
        stored += sizes.header(sealed.actor.as_deref());
        if creates {
            stored += sizes.conversation_row();
        }
        freed += sizes.bookkeeping();
        let mut answer = written(&write.draft, State::Sealed, sealed.records);
        answer.turn = Some(header(&sealed));
        (answer.stored_bytes, answer.freed_bytes) = (stored, freed);
        Ok(answer)
    }

    fn draft_size(&self, conversation_id: &str, stated: Option<u64>, bytes: u64) -> Error {
        conflict(
            conversation_id,
            "draft_size",
            json!({"turn_bytes": stated, "bytes": bytes}),
        )
        .into()
    }

    /// A request naming a sealed draft: its header again for a probe or a
    /// repeat, `draft_sealed` for anything new.
    fn after_seal(
        &self,
        conversation_id: &str,
        sealed: &RecordHeader,
        write: &Write,
    ) -> Result<Written, Error> {
        let turn = || json!({"turn": header(sealed).to_json()});
        let new = write
            .records
            .last()
            .is_some_and(|record| record.seq >= sealed.records);
        if write.discard || new {
            return Err(conflict(conversation_id, "draft_sealed", turn()).into());
        }
        if write
            .actor
            .as_ref()
            .is_some_and(|actor| Some(actor) != sealed.actor.as_ref())
        {
            return Err(conflict(
                conversation_id,
                "draft_actor",
                json!({"draft": write.draft}),
            )
            .into());
        }
        if write
            .turn_bytes
            .is_some_and(|stated| stated != sealed.bytes)
        {
            return Err(self.draft_size(conversation_id, write.turn_bytes, sealed.bytes));
        }
        if let Some(first) = write.records.first() {
            let path = self.record_file(conversation_id, sealed.turn)?;
            let file = drafts::open_records(&path, conversation_id, sealed.turn)?;
            let stored = drafts::digests(file, first.seq, write.records.len(), conversation_id)?;
            for (record, digest) in write.records.iter().zip(&stored) {
                if drafts::hex(&record.digest(None)) != *digest {
                    let details = json!({"next_seq": sealed.records, "seq": record.seq});
                    return Err(conflict(conversation_id, "draft_mismatch", details).into());
                }
            }
        }
        let mut answer = written(&write.draft, State::Sealed, sealed.records);
        answer.turn = Some(header(sealed));
        Ok(answer)
    }
}
