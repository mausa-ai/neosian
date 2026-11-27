//! One queued turn given to its store (DESIGN §40.54 items 15.2, 17.3,
//! 17.4 and 25.13; rulings D8, D10, E8 and E9 of the T1e1 plan), a local
//! store or a server alike: as records in one draft when the store keeps
//! records and they can carry every message, else whole as one
//! `append_turn` (a server without turn records, a 404 on `write_records`,
//! a call id or name over 1,024 bytes).
//!
//! The draft follows the rules of 17.4 (`rules.rs`, `seal.rs`). A draft
//! that does not match the turn (`draft_mismatch`, `draft_size`, a draft
//! longer than the turn) is discarded and the turn written again under the
//! fresh id, once: the state's `attempt` and draft id move first, so a run
//! cut meanwhile resumes the fresh draft, and a refusal of the fresh id
//! holds the turn `mismatch`. A draft that expired (`draft_gap`, 410)
//! starts again from `seq` 0 under the same id, once in a run.
//! `agent_configuration_error` reads the capability again once: gone, the
//! turn goes whole; still there, or the store cannot be opened again, the
//! turn is kept and the destination waits (ruling E15). An answer to the
//! fallback that cannot be read holds the turn `unreadable`: it may be
//! stored, and it is never removed nor sent again by itself (ruling E14).

use std::path::Path;

use super::answer::Answer;
use super::cap::Halt;
use super::draft::fresh_id;
use super::pack::{Draft, turn_bytes};
use super::rules::{Action, Route, Stop, WAIT_SECONDS, decide};
use super::seal::{Sent, send};
use super::sink::{Appended, Sink};
use super::state::{Reason, State};
use crate::conversation::message::Message;
use crate::conversation::turn_records::records_of;
use crate::conversation::{Error, Turn};
use crate::remote::selection::Selection;

/// The reason of a turn whose `append_turn` was answered with a 2xx that
/// could not be read (ruling E14).
pub const UNREADABLE_ANSWER: &str = "a 2xx was answered but could not be read; it may be stored";

/// How a queued turn ended in this run.
#[derive(Debug)]
pub enum Ended {
    /// The store has the turn.
    Stored(Turn),
    /// Held with the reason; the next turn goes on.
    Held(Reason),
    /// The turn stays queued.
    Kept(Stop),
    /// The conversation was paused mid-turn; its draft is discarded.
    Paused,
}

/// The queued turn being landed: its conversation, its opening line (the
/// fresh id is derived from it) and its state file.
pub struct Queued<'a> {
    pub id: &'a str,
    pub line: &'a str,
    pub file: &'a Path,
}

/// `store_until` without a halt, as the tests of `remote/` call it.
#[cfg(test)]
pub fn store(
    sink: &mut Sink,
    selection: &Selection,
    queued: &Queued,
    state: &mut State,
    messages: Vec<Message>,
) -> Result<Ended, Error> {
    store_until(sink, selection, &Halt::NONE, queued, state, messages)
}

/// Gives the queued turn, whose messages are `messages` and whose state is
/// `state`, to the store `sink` opened from `selection`; no part starts
/// once `halt` says to stop.
pub fn store_until(
    sink: &mut Sink,
    selection: &Selection,
    halt: &Halt,
    queued: &Queued,
    state: &mut State,
    messages: Vec<Message>,
) -> Result<Ended, Error> {
    let id = queued.id;
    sink.admit(id)?;
    let (mut restarted, mut reread) = (false, false);
    loop {
        let sent = {
            let Some(records) = sink.records()? else {
                return whole(sink, id, messages, state.actor.clone());
            };
            let Ok(turn) = records_of(&messages) else {
                return whole(sink, id, messages, state.actor.clone());
            };
            let (draft_id, actor) = (state.draft.clone(), state.actor.clone());
            let draft = Draft {
                conversation: id,
                draft: &draft_id,
                actor: &actor,
                turn_bytes: turn_bytes(&turn),
            };
            send(&records, &draft, turn, state, queued.file, halt)?
        };
        match sent {
            Sent::Sealed(header) => {
                return Ok(Ended::Stored(Turn {
                    conversation_id: id.to_string(),
                    turn: header.turn,
                    messages,
                    created_at: header.created_at,
                    actor: header.actor,
                    redacted: false,
                }));
            }
            Sent::Held(reason) => return Ok(Ended::Held(reason)),
            Sent::Fresh(_) if state.attempt == 0 => {
                state.attempt = 1;
                state.draft = fresh_id(id, queued.line);
                state.next_seq = 0;
                state.write(queued.file)?;
            }
            Sent::Fresh(reason) => return Ok(Ended::Held(reason)),
            Sent::Restart if !restarted => {
                restarted = true;
                state.next_seq = 0;
                state.write(queued.file)?;
            }
            Sent::Restart => {
                let message = format!("draft {} expired twice in one run", state.draft);
                return Ok(Ended::Kept(Stop::Conversation(message)));
            }
            Sent::Reread(message) if !reread => {
                reread = true;
                match Sink::open(selection) {
                    Ok(opened) => *sink = opened,
                    Err(error) => return Ok(waits(error.text())),
                }
                if sink.records()?.is_some() {
                    return Ok(waits(message));
                }
            }
            Sent::Reread(message) => return Ok(waits(message)),
            Sent::Whole => return whole(sink, id, messages, state.actor.clone()),
            Sent::Kept(stop) => return Ok(Ended::Kept(stop)),
            Sent::Paused => return Ok(Ended::Paused),
        }
    }
}

/// The turn kept and its destination waiting, as after a fault.
fn waits(message: String) -> Ended {
    Ended::Kept(Stop::Wait {
        seconds: WAIT_SECONDS,
        failing: true,
        message,
    })
}

/// The turn whole, as one `append_turn` (DESIGN §40.54 item 15.2): stored,
/// held for a refused value, a 413 or any other 4xx, kept for what passes.
/// An unreadable 2xx may have stored it, so it is held, never removed and
/// never sent again by itself (ruling E14).
fn whole(sink: &Sink, id: &str, messages: Vec<Message>, actor: String) -> Result<Ended, Error> {
    let answer = match sink.append(id, messages, actor)? {
        Appended::Stored(turn) => return Ok(Ended::Stored(turn)),
        Appended::Not(Answer::Unreadable(_)) => {
            return Ok(Ended::Held(Reason::unreadable(UNREADABLE_ANSWER.into())));
        }
        Appended::Not(answer) => answer,
    };
    Ok(match decide(Route::Whole, &answer) {
        Action::Hold(reason) | Action::HoldAside(reason) | Action::Fresh(reason) => {
            Ended::Held(reason)
        }
        Action::Keep(stop) => Ended::Kept(stop),
        _ => Ended::Kept(Stop::Wait {
            seconds: WAIT_SECONDS,
            failing: true,
            message: format!(
                "append_turn was answered as a records route: {}",
                super::answer::said(&answer)
            ),
        }),
    })
}
