//! A queued turn written as one draft on a store that keeps records, a
//! local store or a server alike (DESIGN §40.54 items 9, 17.3 and 17.4;
//! rulings E8 and E9 of the T1e1 plan). A probe comes first: `sealed`
//! finishes the turn, whose answer was lost; `none` starts from `seq` 0,
//! also after recorded progress (the draft expired); `open` resumes, the
//! first part repeating record `next_seq - 1` so the store's own digest
//! check proves the stored records are this turn's, and nothing before it
//! is sent again. Then the parts of `pack.rs` in order, the confirmed
//! `next_seq` written to the turn's state after each part that does not
//! seal. Every answer goes through the rules of 17.4 (`rules.rs`); what
//! is not a step of this draft is handed back to `run.rs`: a hold, the
//! fresh id, a restart, the turn whole, a stop. No part starts once the
//! lander's ten minutes have passed (`cap.rs`); the turn stays queued
//! with the `next_seq` its last part confirmed. No part starts, and no
//! state is written, unless the lander's admission allows it (`admit.rs`,
//! rulings E19, G1 and G2): once the conversation is paused, or the pause
//! removed the turn, the draft is discarded, best effort, and the turn is
//! handed back as paused; once a profile's capture is off or its policy
//! changed, the turn stays queued as it is. A part already in flight is
//! not recalled, and a seal in flight finishes the turn. In the attempt at
//! a blocked destination (`gate.rs`), the first part's answer decides: a
//! 200 ends the block and the draft goes on; any other answer keeps the
//! turn as it is, with nothing discarded, and ends the run for the
//! destination. The probe is no part of the attempt, but a refusal of it
//! ends the attempt as a part's would, before any discard or hold
//! (ruling G6).

use std::path::Path;

use super::admit::Admit;
use super::answer::said;
use super::cap::{Halt, PASSED};
use super::pack::{Draft, Parts};
use super::rules::{Action, Route, Stop, WAIT_SECONDS, decide};
use super::sink::Records;
use super::state::{MISMATCH, Reason, State};
use crate::conversation::Error;
use crate::conversation::turn_records::{Header, State as DraftState, TurnRecord};

/// How a draft ended.
pub enum Sent {
    /// Sealed, now or by an earlier run: the turn's header.
    Sealed(Header),
    /// Held with the reason, never to be sent again by itself; the draft
    /// was discarded first where the rules say so.
    Held(Reason),
    /// Discarded; to be written again under the fresh id, once (the reason
    /// is held if that is refused too).
    Fresh(Reason),
    /// The draft expired: the turn starts again from `seq` 0.
    Restart,
    /// The store's capability must be read again; the message says why,
    /// for the stop if it is still advertised.
    Reread(String),
    /// The store takes no records here: the turn goes whole.
    Whole,
    /// The turn stays queued.
    Kept(Stop),
    /// The conversation was paused: the draft is discarded.
    Paused,
}

/// Writes `turn`, every record of the queued turn whose state is `state`
/// (at `file`), as the draft `draft` names, until `halt` says to stop.
pub fn send(
    records: &Records,
    draft: &Draft,
    turn: Vec<TurnRecord>,
    state: &mut State,
    file: &Path,
    halt: &Halt,
) -> Result<Sent, Error> {
    send_with(records, draft, turn, state, file, halt, &mut || {})
}

/// `send` with `between` run after each part the store took that does not
/// seal (a sweep, for tests).
pub fn send_with(
    records: &Records,
    draft: &Draft,
    mut turn: Vec<TurnRecord>,
    state: &mut State,
    file: &Path,
    halt: &Halt,
    between: &mut dyn FnMut(),
) -> Result<Sent, Error> {
    let id = draft.conversation;
    let probed = records.write(id, draft.probe())?;
    let action = decide(Route::Records, &probed);
    if let Some(trial) = halt.trying()
        && !matches!(action, Action::Took(..) | Action::Stored(_))
    {
        return Ok(Sent::Kept(trial.refused(action, &probed)));
    }
    let from = match action {
        Action::Took(DraftState::Open, next) => next,
        Action::Took(_, _) => 0,
        other => return ended(records, draft, other),
    };
    let stored = usize::try_from(from).unwrap_or(usize::MAX);
    if stored > turn.len() {
        // Only a draft that is not this turn's holds more than the turn.
        let message = format!(
            "draft {} holds {from} records, more than the turn's {}",
            draft.draft,
            turn.len()
        );
        discard(records, draft);
        return Ok(Sent::Fresh(Reason::answered(MISMATCH, None, None, message)));
    }
    // A resumed part begins with the last record the store holds.
    turn.drain(..stored.saturating_sub(1));
    for part in Parts::new(draft, turn, from == 0) {
        match halt.admit()? {
            Admit::Go => {}
            Admit::Paused => {
                discard(records, draft);
                return Ok(Sent::Paused);
            }
            Admit::Off(message) => return Ok(Sent::Kept(Stop::Conversation(message))),
        }
        if halt.cap.passed() {
            return Ok(Sent::Kept(Stop::Conversation(PASSED.to_string())));
        }
        let write = match part {
            Ok(write) => write,
            Err(message) => {
                discard(records, draft);
                return Ok(Sent::Held(Reason::refused(None, None, message)));
            }
        };
        let seal = write.seal;
        let answer = records.write(id, write)?;
        let action = decide(Route::Records, &answer);
        if let Some(trial) = halt.trying() {
            match action {
                Action::Took(..) | Action::Stored(_) => trial.taken()?,
                action => return Ok(Sent::Kept(trial.refused(action, &answer))),
            }
        }
        match action {
            Action::Took(DraftState::Open, next) if !seal => {
                // The pause removed the turn while the part was in flight.
                if !halt.ours()? {
                    discard(records, draft);
                    return Ok(Sent::Paused);
                }
                state.next_seq = next;
                state.write(file)?;
                between();
            }
            Action::Took(..) => return Ok(broken(said(&answer))),
            other => return ended(records, draft, other),
        }
    }
    Ok(broken(format!(
        "draft {} was not sealed by its last part",
        draft.draft
    )))
}

/// An answer that does not fit the draft's steps (`open` to a seal, `none`
/// to a part), read as an unreadable one: the run stops, the turn stays
/// queued and the next run probes.
fn broken(message: String) -> Sent {
    Sent::Kept(Stop::Wait {
        seconds: WAIT_SECONDS,
        failing: true,
        message,
    })
}

/// What the rules decided, once the draft is done with: discarded where
/// the turn is held or written again, probed for a seal whose header the
/// refusal did not carry.
fn ended(records: &Records, draft: &Draft, action: Action) -> Result<Sent, Error> {
    Ok(match action {
        Action::Stored(header) => Sent::Sealed(header),
        Action::Probe => {
            let probed = decide(
                Route::Records,
                &records.write(draft.conversation, draft.probe())?,
            );
            match probed {
                Action::Stored(header) => Sent::Sealed(header),
                Action::Keep(stop) => Sent::Kept(stop),
                _ => broken(format!("draft {} was sealed without its turn", draft.draft)),
            }
        }
        Action::Hold(reason) => {
            discard(records, draft);
            Sent::Held(reason)
        }
        Action::HoldAside(reason) => Sent::Held(reason),
        Action::Fresh(reason) => {
            discard(records, draft);
            Sent::Fresh(reason)
        }
        Action::Restart => Sent::Restart,
        Action::Reread(message) => Sent::Reread(message),
        Action::Whole => Sent::Whole,
        Action::Keep(stop) => Sent::Kept(stop),
        Action::Took(state, _) => broken(format!(
            "draft {} answered {} where no step was asked",
            draft.draft,
            state.as_str()
        )),
    })
}

/// The draft dropped, best effort: whatever the discard meets, the turn
/// goes on, and a draft left behind expires with the store's own sweep.
fn discard(records: &Records, draft: &Draft) {
    let _ = records.write(draft.conversation, draft.discard());
}
