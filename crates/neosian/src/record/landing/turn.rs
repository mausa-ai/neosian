//! Landing a conversation's queue (DESIGN §40.54 items 7, 17.2, 17.3 and
//! 17.6, rulings D2, D3, E1, E5, E8 and E11 of the T1e1 plan): in `n`
//! order under the conversation's lock. A store that keeps records takes
//! each turn as one draft in parts, then sealed (`seal.rs`); any other
//! store takes it as one `append_turn`, and so does a records store for a
//! turn whose records cannot carry a message (a call id or name over 1,024
//! bytes; format 1 carries it). Once the store has the turn, its files go,
//! then its sessions document is written. A document that cannot be
//! written is reported in the run beside the turns landed and held, and
//! the turn is never sent again. A profile turn whose fingerprint is not
//! the current one is held with `policy_changed`; a direct turn whose
//! destination is not this process's is left pending for a process whose
//! environment names it; a turn that fails its checks is held
//! `unreadable`; a turn refused in a way that would repeat is held
//! `refused` with the refusal's status, code and message. A held or left
//! turn never stops a later one.

use std::fs;
use std::io;

use super::document::{self, Session};
use super::pack::{Draft, turn_bytes};
use super::queue::{Queue, opening_of};
use super::seal::{Sent, send};
use super::sink::Sink;
use super::state::{Destination, POLICY_CHANGED, Reason, State};
use crate::conversation::message::Message;
use crate::conversation::sessions::sessions_path;
use crate::conversation::turn_records::records_of;
use crate::conversation::{Error, Turn};
use crate::record::payload::{Record, last_prompt, messages_of};
use crate::record::spool::{parse, text_of};
use crate::remote::selection::Selection;

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

/// What one run did with the conversation's pending turns: those landed,
/// with their numbers; those refused and held, with the message; and the
/// first sessions document that could not be written (ruling E11: a
/// document failure no longer hides a hold).
#[derive(Default)]
pub struct Run {
    pub landed: Vec<(u64, Recorded)>,
    pub refused: Vec<(u64, String)>,
    pub undocumented: Option<Error>,
}

/// One pending turn's end in this run.
enum Outcome {
    Landed(Recorded),
    /// Stored and removed; its sessions document failed.
    Undocumented(Error),
    /// Refused and held with this message.
    Refused(String),
    /// Held, left for another process, or removed as empty.
    Aside,
}

fn unreadable(error: &impl ToString) -> Reason {
    Reason::unreadable(error.to_string())
}

/// Lands the conversation's pending turns in order and says what became
/// of them; `None` while another process holds the conversation's lock. A
/// store failure stops the run and leaves the rest queued; a document
/// failure is reported in the run, beside the turns landed and held.
pub fn land_queue(queue: &Queue, id: &str, here: &Here) -> Result<Option<Run>, Error> {
    if !queue.pending_dir(id).is_dir() {
        return Ok(Some(Run::default()));
    }
    let Some(lock) = queue.try_lock(id)? else {
        return Ok(None);
    };
    let mut sink = None;
    let mut run = Run::default();
    for n in queue.pending(id)? {
        match land_one(queue, id, n, here, &mut sink)? {
            Outcome::Landed(recorded) => run.landed.push((n, recorded)),
            Outcome::Undocumented(error) => {
                run.undocumented = run.undocumented.or(Some(error));
            }
            Outcome::Refused(message) => run.refused.push((n, message)),
            Outcome::Aside => {}
        }
    }
    drop(sink);
    super::lock::tidy(lock)?;
    Ok(Some(run))
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

/// One pending turn: landed, held, or left for another process. The
/// turn's spool records go once its messages and last prompt are taken, so
/// one copy of its text less is held while it is written.
fn land_one(
    queue: &Queue,
    id: &str,
    n: u64,
    here: &Here,
    sink: &mut Option<Sink>,
) -> Result<Outcome, Error> {
    let Some((mut state, records)) = checked(queue, id, n, here)? else {
        return Ok(Outcome::Aside);
    };
    let prompt = last_prompt(&records);
    let messages = messages_of(&records);
    drop(records);
    if messages.is_empty() {
        queue.remove(id, n)?;
        return Ok(Outcome::Aside);
    }
    let sink = match sink {
        Some(sink) => sink,
        None => sink.insert(Sink::open(here.selection)?),
    };
    let turn = match stored(queue, id, n, sink, &mut state, messages)? {
        Ok(turn) => turn,
        Err(reason) => {
            queue.hold(id, n, &reason)?;
            return Ok(Outcome::Refused(reason.message.unwrap_or_default()));
        }
    };
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
    if let Err(error) = document::write(sink.state(), &session, &turn, prompt.as_deref()) {
        return Ok(Outcome::Undocumented(error));
    }
    Ok(Outcome::Landed(Recorded {
        conversation_id: turn.conversation_id,
        turn: turn.turn,
        document: format!("/{mount}/{}", sessions_path(id)),
        client: sink.state().client().map(str::to_string),
    }))
}

/// The turn given to the store: as records in one draft when it keeps
/// them and the records can carry every message, else one `append_turn`.
/// `Err` holds the turn's refusal, for its reason file.
fn stored(
    queue: &Queue,
    id: &str,
    n: u64,
    sink: &Sink,
    state: &mut State,
    messages: Vec<Message>,
) -> Result<Result<Turn, Reason>, Error> {
    let actor = state.actor.clone();
    let Some(records) = sink.records()? else {
        return Ok(Ok(sink.append_turn(id, messages, actor)?));
    };
    let Ok(turn) = records_of(&messages) else {
        return Ok(Ok(sink.append_turn(id, messages, actor)?));
    };
    let draft_id = state.draft.clone();
    let draft = Draft {
        conversation: id,
        draft: &draft_id,
        actor: &actor,
        turn_bytes: turn_bytes(&turn),
    };
    let file = Queue::part(&queue.pending_dir(id), n, "state");
    Ok(match send(&records, &draft, turn, state, &file)? {
        Sent::Sealed(header) => Ok(Turn {
            conversation_id: id.to_string(),
            turn: header.turn,
            messages,
            created_at: header.created_at,
            actor: header.actor,
            redacted: false,
        }),
        Sent::Held(reason) => Err(reason),
    })
}
