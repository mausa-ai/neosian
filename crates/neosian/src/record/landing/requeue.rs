//! A held turn put back into its conversation's queue, or deleted (DESIGN
//! §40.54 items 17.2 and 17.6; ruling "Held selectors" of the T1e2 plan).
//! Both take the conversation's session lock (`.<key>.lock` of a profile
//! conversation, `.<id>.lock` of a direct one, in the spool tree), which a
//! Stop and a pause take, then its landing lock without waiting, in the
//! order a Stop takes them; a turn whose landing lock another process
//! holds stays as it is. Under both, the turn is read again: it must still
//! be there and begin with the opening line it was chosen by.
//!
//! A retry gives the turn its conversation's next number, never one a
//! lander may hold (ruling G1 of the T1e1 plan), so it goes behind every
//! queued turn. Its bytes, `closed_at`, draft id, `attempt` and confirmed
//! `next_seq` stay as they were, and its reason file goes with it: beside a
//! pending turn it tells the keep limit that this turn's number does not
//! follow when it closed (`keep.rs`). The pending state and reason are
//! written and synced first, then the turn moves by one rename, the move
//! is synced, and only then do the held state and reason go: a cut before
//! the rename leaves the held turn whole and pending files without their
//! turn, a cut after it the pending turn whole and held files without
//! their turn, and a later listing removes what is left (`files::sweep`).
//! A retry sends nothing: the next hook event that starts a lander for the
//! turn's destination lands it through ordinary admission.
//!
//! A turn goes back only under the destination and the policy it was
//! closed for. A direct turn keeps its destination. A profile turn goes
//! back only when its profile, as found here, has the fingerprint the turn
//! was closed with and derives the turn's conversation id; a turn held
//! because its policy changed then takes its destination, scope, mount,
//! provenance and project from that profile, and from nothing else. A
//! paused conversation's turn, a turn whose state, reason, length, opening
//! line or draft id does not hold, and a turn whose earlier answer may have
//! stored it (ruling E14) without `duplicate`, stay held, and the outcome
//! says why.
//!
//! A discard deletes the turn, then its state and reason, and reports the
//! bytes of its file.

use std::fs::{self, File};
use std::io;
use std::path::Path;

use super::aside::{opening, said};
use super::draft::{draft_id, fresh_id};
use super::files::sync_directory;
use super::lock::tidy;
use super::queue::Queue;
use super::run::UNREADABLE_ANSWER;
use super::state::{Destination, State, UNREADABLE};
use crate::record::spool::Spool;
use crate::store::fileio::{atomic_write, remove_file};

/// Why a turn stays held: it is no longer there or not the one chosen.
pub const GONE: &str = "gone";
/// Another process holds its conversation's landing lock.
pub const BUSY: &str = "busy";
pub const PAUSED: &str = "paused";
/// Its state, length, opening line or draft id does not hold.
pub const DAMAGED: &str = "damaged";
/// Its earlier answer may have stored it (ruling E14).
pub const MAY_BE_STORED: &str = "may_be_stored";
/// Its profile is not found here with the policy it was closed under.
pub const POLICY: &str = "policy_differs";

/// What became of one held turn.
#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Back in the queue as number `n`; its file holds `bytes`.
    Retried { n: u64, bytes: u64 },
    /// Deleted; its file held `bytes`.
    Discarded { bytes: u64 },
    /// Still held: a code above and a sentence.
    Stays { code: &'static str, why: String },
}

fn stays(code: &'static str, why: &str) -> Outcome {
    Outcome::Stays {
        code,
        why: why.to_string(),
    }
}

/// A profile as the caller finds it for a profile turn.
pub struct Found {
    /// The fingerprint of its policy now.
    pub fingerprint: String,
    /// The conversation id it derives for the turn's agent and session.
    pub conversation_id: String,
    pub destination: Destination,
    pub scope: String,
    pub mount: String,
    pub provenance: String,
    pub project: String,
}

/// What a retry is allowed: a turn whose answer may have stored it, and
/// how the profile of a profile turn is found (`Err` says why it is not).
pub struct Ask<'a> {
    pub duplicate: bool,
    pub find: &'a dyn Fn(&State) -> Result<Found, String>,
}

/// A held turn as it was chosen: its spool tree, conversation, number and
/// the opening line its file began with.
pub struct Chosen<'a> {
    pub tree: &'a Path,
    pub id: &'a str,
    pub n: u64,
    pub line: Option<&'a str>,
}

/// The session lock a Stop and a pause take for the conversation.
fn session(chosen: &Chosen) -> io::Result<File> {
    let name = crate::record::pause::session_key(chosen.id).unwrap_or(chosen.id);
    Spool::new(chosen.tree).lock(name)
}

/// Runs `then` with the turn under both locks and its file's length, once
/// it is still there and still the turn chosen.
fn locked(
    chosen: &Chosen,
    then: impl FnOnce(&Queue, u64) -> io::Result<Outcome>,
) -> io::Result<Outcome> {
    let _session = session(chosen)?;
    let queue = Queue::new(chosen.tree);
    let Some(lock) = queue.try_lock(chosen.id)? else {
        return Ok(stays(BUSY, "another process holds its conversation"));
    };
    let file = Queue::part(&queue.held_dir(chosen.id), chosen.n, "jsonl");
    let outcome = match fs::symlink_metadata(&file) {
        Ok(_) if opening(&file)?.as_deref() != chosen.line => {
            Ok(stays(GONE, "another turn has its number now"))
        }
        Ok(metadata) => then(&queue, metadata.len()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            Ok(stays(GONE, "it is no longer held"))
        }
        Err(error) => Err(error),
    };
    tidy(lock)?;
    outcome
}

/// Deletes the chosen held turn: its file, then its state and reason.
pub fn discard(chosen: &Chosen) -> io::Result<Outcome> {
    locked(chosen, |queue, bytes| {
        let directory = queue.held_dir(chosen.id);
        for part in ["jsonl", "state", "reason"] {
            remove_file(&Queue::part(&directory, chosen.n, part))?;
        }
        sync_directory(&directory)?;
        let _ = fs::remove_dir(&directory);
        Ok(Outcome::Discarded { bytes })
    })
}

/// Puts the chosen held turn back at the end of its conversation's queue
/// when it may go, as the module says; `cut` runs with `"staged"` before
/// the rename and `"moved"` after it (a cut, for tests).
pub fn retry(
    chosen: &Chosen,
    ask: &Ask,
    mut cut: impl FnMut(&str) -> io::Result<()>,
) -> io::Result<Outcome> {
    locked(chosen, |queue, bytes| {
        let (id, n) = (chosen.id, chosen.n);
        if queue.paused(id)? {
            return Ok(stays(PAUSED, "its conversation is paused"));
        }
        let directory = queue.held_dir(id);
        let mut state = match State::read(&Queue::part(&directory, n, "state")) {
            Ok(state) if state.conversation_id == id => state,
            _ => return Ok(stays(DAMAGED, "its state cannot be read")),
        };
        let Some(line) = chosen.line.filter(|_| state.bytes == bytes) else {
            return Ok(stays(DAMAGED, "its file is not the turn its state names"));
        };
        let draft = match state.attempt {
            0 => draft_id(id, line),
            1 => fresh_id(id, line),
            _ => String::new(),
        };
        if state.draft != draft {
            return Ok(stays(DAMAGED, "its draft id is not this turn's"));
        }
        let Some(said) = said(&directory, n) else {
            return Ok(stays(DAMAGED, "its reason cannot be read"));
        };
        let answered =
            said.reason == UNREADABLE && said.message.as_deref() == Some(UNREADABLE_ANSWER);
        if answered && !ask.duplicate {
            let why = "a 2xx was answered for it but could not be read, so the store may \
                       hold it already; --allow-duplicate retries it anyway";
            return Ok(stays(MAY_BE_STORED, why));
        }
        if let Some(theirs) = state.fingerprint.clone() {
            let found = match (ask.find)(&state) {
                Ok(found) => found,
                Err(why) => return Ok(stays(POLICY, &why)),
            };
            if found.fingerprint != theirs {
                let why = "the policy it was recorded under does not apply here now";
                return Ok(stays(POLICY, why));
            }
            if found.conversation_id != id {
                return Ok(stays(POLICY, "the profile found here did not record it"));
            }
            match &state.destination {
                None => fill(&mut state, found),
                Some(destination) if *destination == found.destination => {}
                Some(_) => return Ok(stays(POLICY, "its profile names another destination now")),
            }
        } else if state.destination.is_none() {
            return Ok(stays(DAMAGED, "its state names no destination"));
        }
        let n = move_back(queue, id, n, &state, &mut cut)?;
        Ok(Outcome::Retried { n, bytes })
    })
}

/// The routing a turn held because its policy changed takes from the
/// profile found for it: only what its state lacks.
fn fill(state: &mut State, found: Found) {
    state.destination = Some(found.destination);
    state.scope = state.scope.take().or(Some(found.scope));
    state.mount = state.mount.take().or(Some(found.mount));
    state.provenance = state.provenance.take().or(Some(found.provenance));
    state.project = state.project.take().or(Some(found.project));
}

/// Moves held turn `n` back to `pending/` under the conversation's next
/// number with `state`: the state and the reason written and synced, the
/// turn renamed and the move synced, then the held state and reason
/// removed. The new number.
fn move_back(
    queue: &Queue,
    id: &str,
    n: u64,
    state: &State,
    cut: &mut impl FnMut(&str) -> io::Result<()>,
) -> io::Result<u64> {
    let (from, to) = (queue.held_dir(id), queue.pending_dir(id));
    let next = queue.next_n(id)?;
    let reason = fs::read_to_string(Queue::part(&from, n, "reason"))?;
    atomic_write(&Queue::part(&to, next, "state"), &state.text(), true)?;
    atomic_write(&Queue::part(&to, next, "reason"), &reason, true)?;
    sync_directory(&to)?;
    cut("staged")?;
    fs::rename(
        Queue::part(&from, n, "jsonl"),
        Queue::part(&to, next, "jsonl"),
    )?;
    queue.persist(&to, next)?;
    sync_directory(&from)?;
    cut("moved")?;
    remove_file(&Queue::part(&from, n, "state"))?;
    remove_file(&Queue::part(&from, n, "reason"))?;
    sync_directory(&from)?;
    let _ = fs::remove_dir(&from);
    Ok(next)
}
