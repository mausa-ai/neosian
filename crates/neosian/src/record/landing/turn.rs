//! Landing a conversation's queue (DESIGN §40.54 items 7, 17.2, 17.3,
//! 17.4 and 17.6, rulings D2, D3, E1, E5 and E11 of the T1e1 plan): in
//! `n` order under the conversation's lock. Each turn goes to its store as
//! `run.rs` gives it, a local store or a server alike: as one draft in
//! parts where the store keeps records, else whole. Once the store has the
//! turn, its files go, then its sessions document is written. A document
//! that cannot be written is reported in the run beside the turns landed
//! and held, and the turn is never sent again. A profile turn whose
//! fingerprint is not the current one is held with `policy_changed`; a
//! direct turn whose destination is not this process's is left pending for
//! a process whose environment names it; a turn that fails its checks is
//! held `unreadable`; a turn refused in a way that would repeat is held
//! with the refusal's status, code and message. A held or left turn never
//! stops a later one; a turn that stays queued stops the conversation, so
//! no later turn overtakes it. Before each turn the run reads under the
//! conversation's lock whether the conversation is paused and whether its
//! ten minutes passed, and a lander, which holds no session lock, reads its
//! admission of the turn before each write (`admit.rs`; rulings O2, E19, G1
//! and G2). A turn paused between parts has its draft discarded and its
//! files removed unless the pause removed them; one stored meanwhile gets
//! no sessions document. The cap leaves the rest queued for the next hook
//! event. A held turn's reason is cleaned first (`kept.rs`).

use std::fs;
use std::io;

use super::admit::{Admission, Admit};
use super::cap::{Cap, Halt, PASSED, Patience};
use super::document::{self, Session};
use super::draft::belongs;
use super::gate::Trial;
use super::kept::{self, credentials};
use super::queue::{Queue, opening_of};
use super::rules::{Stop, unopened};
use super::run::{Ended, Queued, store_until};
use super::serving::Serving;
use super::sink::Sink;
use super::state::{Destination, POLICY_CHANGED, Reason, State};
use crate::conversation::Error;
use crate::conversation::sessions::sessions_path;
use crate::record::payload::{Record, last_prompt, messages_of};
use crate::record::spool::{parse, text_of};
use crate::remote::selection::Selection;

/// What this process can serve, and what a turn closed under a policy
/// it no longer knows takes from it; for a lander, its `Retry-After`
/// budget, the attempt at a blocked destination (`gate.rs`) and the
/// destination as it serves it, which its admission reads again
/// (`admit.rs`; `None` for a Stop's inline landing).
#[derive(Clone)]
pub struct Here<'a> {
    pub selection: &'a Selection,
    pub destination: Destination,
    pub fingerprint: Option<&'a str>,
    pub scope: &'a str,
    pub mount: &'a str,
    pub provenance: Option<&'a str>,
    pub cap: &'a Cap,
    pub patience: Option<&'a Patience>,
    pub trial: Option<&'a Trial<'a>>,
    pub serving: Option<&'a Serving>,
}

/// A landed turn, as the envelope reports it; no document when the
/// conversation was paused, or its profile's capture or policy changed,
/// once the store had the turn.
pub struct Recorded {
    pub conversation_id: String,
    pub turn: u64,
    pub document: Option<String>,
    pub client: Option<String>,
}

/// What one run did with the conversation's pending turns: those landed,
/// with their numbers; those refused and held, with the message; the
/// first sessions document that could not be written (ruling E11: a
/// document failure no longer hides a hold); and why the run stopped at a
/// turn that stays queued, with every later one.
#[derive(Default)]
pub struct Run {
    pub landed: Vec<(u64, Recorded)>,
    pub refused: Vec<(u64, String)>,
    pub undocumented: Option<Error>,
    pub stopped: Option<Stop>,
    /// The conversation is paused: nothing more of it was landed.
    pub paused: bool,
}

/// One pending turn's end in this run.
enum Outcome {
    Landed(Recorded),
    /// Stored and removed; its sessions document failed.
    Undocumented(Error),
    /// Refused and held with this message.
    Refused(String),
    /// Kept in the queue: this run lands nothing more of the conversation.
    Kept(Stop),
    /// Held, left for another process, or removed as empty.
    Aside,
    /// The conversation was paused mid-turn: the turn was removed.
    Paused,
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
        if queue.paused(id)? {
            run.paused = true;
            break;
        }
        if here.cap.passed() {
            run.stopped = Some(Stop::Conversation(PASSED.to_string()));
            break;
        }
        match land_one(queue, id, n, here, &mut sink)? {
            Outcome::Landed(recorded) => run.landed.push((n, recorded)),
            Outcome::Undocumented(error) => {
                run.undocumented = run.undocumented.or(Some(error));
            }
            Outcome::Refused(message) => run.refused.push((n, message)),
            Outcome::Kept(stop) => {
                run.stopped = Some(stop);
                break;
            }
            Outcome::Aside => {}
            Outcome::Paused => {
                run.paused = true;
                break;
            }
        }
    }
    drop(sink);
    super::lock::tidy(lock)?;
    Ok(Some(run))
}

/// Pending turn `n` with its records once it passes its checks (its
/// state, its policy or destination, its length, its opening line and the
/// draft id it gives, its records); otherwise it is held, or left when it
/// is another destination's, and `None` is returned.
fn checked(
    queue: &Queue,
    id: &str,
    n: u64,
    here: &Here,
) -> Result<Option<(State, String, Vec<Record>)>, Error> {
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
    let Some(line) = opening_of(&bytes).map(str::to_string) else {
        return Ok(aside(unreadable(&"the turn has no opening line"))?);
    };
    if !belongs(&state.draft, id, &line, state.attempt) {
        return Ok(aside(unreadable(&"its draft id is not this turn's"))?);
    }
    match text_of(&bytes).and_then(|text| parse(text, &file)) {
        Ok(records) => Ok(Some((state, line, records))),
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
    let Some((mut state, line, records)) = checked(queue, id, n, here)? else {
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
        // A store that cannot be opened or reached keeps the turn queued,
        // as a fault of its destination, or a credential it refused (17.4).
        None => match Sink::open(here.selection) {
            Ok(opened) => sink.insert(opened),
            Err(error) => return Ok(Outcome::Kept(unopened(&error))),
        },
    };
    let directory = queue.pending_dir(id);
    let file = Queue::part(&directory, n, "state");
    let span = Queue::part(&directory, n, "jsonl");
    let queued = Queued {
        id,
        line: &line,
        file: &file,
    };
    let marker = queue.marker(id);
    let admission = here.serving.map(|serving| Admission {
        turn: &span,
        line: &line,
        paused: &marker,
        serving: Some(serving),
    });
    let halt = Halt {
        cap: here.cap,
        admission: admission.as_ref(),
        patience: here.patience,
        trial: here.trial,
    };
    let (ended, landed) = store_until(sink, here.selection, &halt, &queued, &mut state, messages)?;
    let turn = match ended {
        Ended::Stored(turn) => turn,
        // The pause removed the turn: there is nothing left to hold.
        Ended::Held(_) if !halt.ours()? => return Ok(Outcome::Paused),
        Ended::Held(reason) => {
            let reason = kept::reason(reason, &credentials(here.selection));
            queue.hold(id, n, &reason)?;
            return Ok(Outcome::Refused(reason.message.unwrap_or_default()));
        }
        Ended::Kept(stop) => return Ok(Outcome::Kept(stop)),
        // As the pause removes the turns it finds (ruling E19).
        Ended::Paused => {
            if halt.ours()? {
                queue.remove(id, n)?;
            }
            return Ok(Outcome::Paused);
        }
    };
    // Stored: the turn is never sent again, whatever its document does.
    let ours = halt.ours()?;
    if ours {
        queue.remove(id, n)?;
    }
    let documented = ours && halt.allows()? == Admit::Go;
    let mount = state.mount.as_deref().unwrap_or(here.mount);
    let session = Session {
        agent: &state.agent,
        conversation_id: id,
        actor: &state.actor,
        scope: state.scope.as_deref().unwrap_or(here.scope),
        parent: state.parent.as_deref(),
        provenance: state.provenance.as_deref().or(here.provenance),
    };
    if documented {
        let written = sink.first_turn_stamp(id).and_then(|first| {
            let (store, prompt, landed) = (sink.state(), prompt.as_deref(), landed.as_ref());
            document::write(store, &session, &turn, prompt, first, landed)
        });
        if let Err(error) = written {
            return Ok(Outcome::Undocumented(error));
        }
    }
    Ok(Outcome::Landed(Recorded {
        conversation_id: turn.conversation_id,
        turn: turn.turn,
        document: documented.then(|| format!("/{mount}/{}", sessions_path(id))),
        client: sink.state().client().map(str::to_string),
    }))
}
