//! A queued turn written as one draft on a store that keeps records
//! (DESIGN §40.54 items 9, 17.3 and 17.4 for a local store; rulings E8 and
//! E9 of the T1e1 plan). A probe comes first: `sealed` finishes the turn,
//! whose answer was lost; `none` starts from `seq` 0; `open` resumes, the
//! first part repeating record `next_seq - 1` so the store's own digest
//! check proves the stored records are this turn's. Then the parts of
//! `pack.rs` in order, the confirmed `next_seq` written to the turn's state
//! after each part that does not seal.
//!
//! A refusal that will be answered again whatever is retried holds the
//! turn `refused`, after discarding its draft (best effort): a refused value
//! (`value_error` at 400 or 413, any 422, `NEOSIAN_MAX_RECORD_TURN_BYTES`
//! included), `draft_size`, `draft_mismatch`, a draft of this turn that the
//! store cannot read, and a draft longer than the turn; so is a part the
//! packer refuses. Any other refusal (`drafts_open`, a `draft_gap` after
//! the draft was swept) stops the run with the turn queued, and the next
//! run probes again. T1e1c's rules table replaces this with 17.4's rows.

use std::path::Path;

use serde_json::Value;

use super::pack::{Draft, Parts};
use super::sink::{Answer, Records, VALUE_ERROR};
use super::state::{Reason, State};
use crate::conversation::Error;
use crate::conversation::turn_records::{Header, State as DraftState, TurnRecord, Written};
use crate::remote::records::Refusal;

const CONFLICT: &str = "agent_conversation_conflict";
const FORMAT: &str = "agent_conversation_format_unsupported";

/// How a draft ended.
pub enum Sent {
    /// Sealed, now or by an earlier run: the turn's header.
    Sealed(Header),
    /// Refused, never to be sent again by itself: why, for `<n>.reason`.
    Held(Reason),
}

/// Writes `turn`, every record of the queued turn whose state is `state`
/// (at `file`), as the draft `draft` names.
pub fn send(
    records: &Records,
    draft: &Draft,
    turn: Vec<TurnRecord>,
    state: &mut State,
    file: &Path,
) -> Result<Sent, Error> {
    send_with(records, draft, turn, state, file, &mut || {})
}

/// `send` with `between` run after each part the store took that does not
/// seal (a sweep, for tests).
pub fn send_with(
    records: &Records,
    draft: &Draft,
    mut turn: Vec<TurnRecord>,
    state: &mut State,
    file: &Path,
    between: &mut dyn FnMut(),
) -> Result<Sent, Error> {
    let id = draft.conversation;
    let from = match records.write(id, draft.probe())? {
        Answer::Written(written) => match written.state {
            DraftState::Sealed => return sealed(written),
            DraftState::Open => written.next_seq,
            DraftState::None | DraftState::Discarded => 0,
        },
        Answer::Refused(refusal) => return refused(records, draft, refusal),
    };
    let stored = usize::try_from(from).unwrap_or(usize::MAX);
    if stored > turn.len() {
        let message = format!(
            "draft {} holds {from} records, more than the turn's {}",
            draft.draft,
            turn.len()
        );
        discard(records, draft);
        return Ok(Sent::Held(Reason::refused(None, None, message)));
    }
    // A resumed part begins with the last record the store holds.
    turn.drain(..stored.saturating_sub(1));
    for part in Parts::new(draft, turn, from == 0) {
        let write = match part {
            Ok(write) => write,
            Err(message) => {
                discard(records, draft);
                return Ok(Sent::Held(Reason::refused(None, None, message)));
            }
        };
        let seal = write.seal;
        match records.write(id, write)? {
            Answer::Written(written) if written.state == DraftState::Sealed => {
                return sealed(written);
            }
            Answer::Written(written) if written.state == DraftState::Open && !seal => {
                state.next_seq = written.next_seq;
                state.write(file)?;
                between();
            }
            Answer::Written(written) => {
                let answered = written.state.as_str();
                return Err(Error::Value(format!(
                    "draft {} answered {answered} to a part",
                    draft.draft
                )));
            }
            Answer::Refused(refusal) => return refused(records, draft, refusal),
        }
    }
    Err(Error::Value(format!(
        "draft {} was not sealed by its last part",
        draft.draft
    )))
}

fn sealed(written: Written) -> Result<Sent, Error> {
    match written.turn {
        Some(header) => Ok(Sent::Sealed(header)),
        None => Err(Error::Value(format!(
            "draft {} answered sealed without its turn",
            written.draft
        ))),
    }
}

/// The draft dropped, best effort: whatever the discard meets, the turn
/// is held, and a draft left behind expires with the store's own sweep.
fn discard(records: &Records, draft: &Draft) {
    let _ = records.write(draft.conversation, draft.discard());
}

/// Whether the same request would be refused again (ruling E8). Every
/// request names this turn's draft alone, so an unreadable draft, record
/// or range in a refusal is this turn's.
fn lasting(refusal: &Refusal) -> bool {
    let code = refusal.code.as_deref();
    let reason = refusal.details.get("reason").and_then(Value::as_str);
    let reason = reason.unwrap_or_default();
    let value = code == Some(VALUE_ERROR) && matches!(refusal.status, 400 | 413);
    let conflict = code == Some(CONFLICT) && matches!(reason, "draft_size" | "draft_mismatch");
    let unreadable = code == Some(FORMAT)
        && (reason.contains("draft's")
            || reason.starts_with("record ")
            || reason.starts_with("records line ")
            || reason.starts_with("the requested range of stored records"));
    value || refusal.status == 422 || conflict || unreadable
}

/// A lasting refusal holds the turn; anything else stops the run.
fn refused(records: &Records, draft: &Draft, refusal: Refusal) -> Result<Sent, Error> {
    if lasting(&refusal) {
        discard(records, draft);
        let reason = Reason::refused(Some(refusal.status), refusal.code, refusal.message);
        return Ok(Sent::Held(reason));
    }
    let code = refusal.code.as_deref().unwrap_or("no code");
    Err(Error::Value(format!("[{code}] {}", refusal.message)))
}
