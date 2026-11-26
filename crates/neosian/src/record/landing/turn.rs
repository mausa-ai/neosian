//! Landing a conversation's queue (DESIGN §40.54 items 17.2, 17.3 and
//! 17.6, rulings D2, D3, E1 and E5 of the T1e1 plan): in `n` order under
//! the conversation's lock, each turn as one `append_turn` (the wire is
//! unchanged until records arrive); once the store answers, the turn's
//! files go, then its sessions document is written. A document that
//! cannot be written is reported after the run, and the turn is never sent
//! again. A profile turn whose fingerprint is not the current one is held
//! with `policy_changed`; a direct turn whose destination is not this
//! process's is left pending for a process whose environment names it; a
//! turn that fails its checks is held `unreadable`. A held or left turn
//! never stops a later one.

use std::fs;
use std::io;
use std::sync::Arc;

use super::document::{self, Session};
use super::queue::{Queue, opening_of};
use super::state::{Destination, POLICY_CHANGED, Reason, State, UNREADABLE};
use crate::conversation::Error;
use crate::conversation::sessions::sessions_path;
use crate::record::payload::{Record, last_prompt, messages_of};
use crate::record::spool::{parse, text_of};
use crate::remote::selection::Selection;
use crate::state::State as Store;

/// What this process can serve, and what a turn closed under a policy
/// it no longer knows takes from it.
pub struct Here<'a> {
    pub selection: &'a Selection,
    pub destination: Destination,
    pub fingerprint: Option<&'a str>,
    pub scope: &'a str,
    pub mount: &'a str,
    pub provenance: Option<&'a str>,
}

/// A landed turn, as the envelope reports it.
pub struct Recorded {
    pub conversation_id: String,
    pub turn: u64,
    pub document: String,
    pub client: Option<String>,
}

/// One pending turn's end in this run.
enum Outcome {
    Landed(Recorded),
    /// Stored and removed; its sessions document failed.
    Undocumented(Error),
    /// Held, left for another process, or removed as empty.
    Aside,
}

fn unreadable(error: &impl ToString) -> Reason {
    Reason {
        reason: UNREADABLE,
        message: Some(error.to_string()),
    }
}

/// Lands the conversation's pending turns in order and returns those
/// landed with their numbers; `None` while another process holds the
/// conversation's lock. A store failure stops the run and leaves the rest
/// queued; a document failure is returned after the run.
pub fn land_queue(
    queue: &Queue,
    id: &str,
    here: &Here,
) -> Result<Option<Vec<(u64, Recorded)>>, Error> {
    if !queue.pending_dir(id).is_dir() {
        return Ok(Some(Vec::new()));
    }
    let Some(lock) = queue.try_lock(id)? else {
        return Ok(None);
    };
    let mut store = None;
    let mut landed = Vec::new();
    let mut failure = None;
    for n in queue.pending(id)? {
        match land_one(queue, id, n, here, &mut store)? {
            Outcome::Landed(recorded) => landed.push((n, recorded)),
            Outcome::Undocumented(error) => failure = failure.or(Some(error)),
            Outcome::Aside => {}
        }
    }
    drop(store);
    super::lock::tidy(lock)?;
    match failure {
        Some(error) => Err(error),
        None => Ok(Some(landed)),
    }
}

/// Pending turn `n` with its records once it passes its checks (its
/// state, its policy or destination, its length, its opening line, its
/// records); otherwise it is held, or left when it is another
/// destination's, and `None` is returned.
fn checked(
    queue: &Queue,
    id: &str,
    n: u64,
    here: &Here,
) -> Result<Option<(State, Vec<Record>)>, Error> {
    let directory = queue.pending_dir(id);
    let aside = |reason: Reason| queue.hold(id, n, &reason).map(|()| None);
    let state = match State::read(&Queue::part(&directory, n, "state")) {
        Ok(state) if state.conversation_id == id => state,
        Ok(_) => return Ok(aside(unreadable(&"the state names another conversation"))?),
        Err(error)
            if matches!(
                error.kind(),
                io::ErrorKind::NotFound | io::ErrorKind::InvalidData
            ) =>
        {
            return Ok(aside(unreadable(&error))?);
        }
        Err(error) => return Err(error.into()),
    };
    match &state.fingerprint {
        Some(theirs) if here.fingerprint != Some(theirs.as_str()) => {
            return Ok(aside(Reason::of(POLICY_CHANGED))?);
        }
        None if state.destination.as_ref() != Some(&here.destination) => return Ok(None),
        _ => {}
    }
    let file = Queue::part(&directory, n, "jsonl");
    let bytes = fs::read(&file)?;
    if bytes.len() as u64 != state.bytes {
        let why = format!(
            "the turn holds {} bytes, its state {}",
            bytes.len(),
            state.bytes
        );
        return Ok(aside(unreadable(&why))?);
    }
    if opening_of(&bytes).is_none() {
        return Ok(aside(unreadable(&"the turn has no opening line"))?);
    }
    match text_of(&bytes).and_then(|text| parse(text, &file)) {
        Ok(records) => Ok(Some((state, records))),
        Err(error) => Ok(aside(unreadable(&error))?),
    }
}

/// One pending turn: landed, held, or left for another process.
fn land_one(
    queue: &Queue,
    id: &str,
    n: u64,
    here: &Here,
    store: &mut Option<Arc<dyn Store>>,
) -> Result<Outcome, Error> {
    let Some((state, records)) = checked(queue, id, n, here)? else {
        return Ok(Outcome::Aside);
    };
    let messages = messages_of(&records);
    if messages.is_empty() {
        queue.remove(id, n)?;
        return Ok(Outcome::Aside);
    }
    let store = match store {
        Some(store) => Arc::clone(store),
        None => Arc::clone(store.insert(here.selection.open()?)),
    };
    let turn = store.append_turn(id, messages, Some(state.actor.clone()))?;
    // Stored: the turn is never sent again, whatever its document does.
    queue.remove(id, n)?;
    let mount = state.mount.as_deref().unwrap_or(here.mount);
    let session = Session {
        agent: &state.agent,
        conversation_id: id,
        actor: &state.actor,
        scope: state.scope.as_deref().unwrap_or(here.scope),
        parent: state.parent.as_deref(),
        provenance: state.provenance.as_deref().or(here.provenance),
    };
    let prompt = last_prompt(&records);
    if let Err(error) = document::write(store.as_ref(), &session, &turn, prompt.as_deref()) {
        return Ok(Outcome::Undocumented(error));
    }
    Ok(Outcome::Landed(Recorded {
        conversation_id: turn.conversation_id,
        turn: turn.turn,
        document: format!("/{mount}/{}", sessions_path(id)),
        client: store.client().map(str::to_string),
    }))
}
