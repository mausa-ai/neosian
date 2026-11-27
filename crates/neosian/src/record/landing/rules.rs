//! The rows of 17.4 as one table (DESIGN §40.54 items 17.3, 17.4 and
//! 25.13; rulings D10, E8 and E9 of the T1e1 plan): from an answer to what
//! the lander does with the turn. It reads a local store and a server alike
//! (`answer.rs`), and the answers to `write_records` and to the fallback
//! `append_turn` with the same rows; nothing here sends, waits or writes.
//!
//! Where 17.4 and ruling E8 of T1e1b differ, 17.4 governs both a local
//! store and a server: `draft_mismatch` and `draft_size` write the turn
//! again under the fresh id once and hold it only at the second refusal,
//! and `draft_gap` starts the draft again from `seq` 0. Answers 17.4 does
//! not name follow D10 as ruled by E15: any other 4xx holds the turn with
//! the message; `agent_configuration_error` on `write_records` reads the
//! capability again, and a capability still advertised keeps the turn and
//! stops the destination; a 3xx, and a 404 on the fallback `append_turn`,
//! block as a 403 does; an `agent_conversation_format_unsupported` that
//! names the turn's own draft gets the fresh id as `draft_mismatch` does,
//! while one about the conversation holds; a fault on this side waits,
//! unless it is the credential, which blocks as a 401 does. A store that
//! cannot be opened waits too, unless its handshake refused the credential
//! or no credential could be read for it (`unopened`): that blocks as a
//! 401 does (the T1e1c note on the handshake).

use serde_json::Value;

use super::answer::{Answer, said};
use super::state::{ACTOR_CHANGED, MISMATCH, REFUSED, Reason};
use crate::conversation::Error;
use crate::conversation::turn_records::{Header, State as DraftState, Written};
use crate::remote::records::Refusal;

const CONFLICT: &str = "agent_conversation_conflict";
const CONFIGURATION: &str = "agent_configuration_error";
const FORMAT: &str = "agent_conversation_format_unsupported";
const STORAGE: &str = "storage_exceeded";
/// The longest `Retry-After` a run waits out (17.3).
pub const RETRY_SECONDS: u64 = 10;
/// How long a destination waits after a fault or a bare 429 (17.4).
pub const WAIT_SECONDS: u64 = 60;

/// The request an answer is to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Route {
    /// `write_records`: a probe, a part, a seal or a discard.
    Records,
    /// The turn whole, as one `append_turn`.
    Whole,
}

/// What the lander does with the turn.
#[derive(Debug, PartialEq, Eq)]
pub enum Action {
    /// 200: the draft's state and `next_seq`.
    Took(DraftState, u64),
    /// The turn is stored: 200 sealed, or `draft_sealed` with the header
    /// it carries (an earlier answer was lost).
    Stored(Header),
    /// `draft_sealed` without its header: a probe answers it.
    Probe,
    /// The draft discarded (best effort) and the turn held with the
    /// reason; the next turn goes on.
    Hold(Reason),
    /// The turn held without a discard (`draft_actor`: the draft is
    /// another actor's, and a discard naming this one is refused too).
    HoldAside(Reason),
    /// `draft_mismatch` or `draft_size`: the draft discarded and the turn
    /// written again under the fresh id, once; the reason is held if the
    /// fresh id is refused too.
    Fresh(Reason),
    /// `draft_gap` or 410: the draft expired; the turn starts again from
    /// `seq` 0 under the same id.
    Restart,
    /// `agent_configuration_error` on `write_records`: the capability read
    /// again once; gone, the turn goes whole; still there, the turn is kept
    /// and the destination waits, with this message (ruling E15).
    Reread(String),
    /// No turn records, or 404 on `write_records`: one `append_turn`.
    Whole,
    /// The turn stays queued; how far the stop reaches.
    Keep(Stop),
}

/// Why a turn stays queued, and whether the rest of the run may go on.
#[derive(Debug, PartialEq, Eq)]
pub enum Stop {
    /// `drafts_open`: this conversation waits for the next run.
    Conversation(String),
    /// 429 with `Retry-After` of at most 10 s: wait and repeat the part.
    Retry { seconds: u64, message: String },
    /// The destination waits `seconds`: a longer or absent `Retry-After`,
    /// or a fault (`failing`: 5xx, no answer, an unreadable answer, a
    /// fault on this side).
    Wait {
        seconds: u64,
        failing: bool,
        message: String,
    },
    /// The destination is blocked until it resumes (17.5), with the status
    /// and code of the answer that blocked it, which its record keeps.
    Block {
        block: Block,
        message: String,
        status: Option<u16>,
        code: Option<String>,
    },
}

/// What a block is for, so the right resume ends it (17.5).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Block {
    /// 413 `storage_exceeded`: the account's storage is full.
    Storage,
    /// 403, a 3xx, or a 404 on the fallback `append_turn`: writes are
    /// refused, or this is not the service's address.
    Forbidden,
    /// 401, or a credential this side cannot read: sign in again.
    Credential,
}

impl Block {
    /// The name a destination's record gives the block.
    pub fn name(self) -> &'static str {
        match self {
            Self::Storage => "storage",
            Self::Forbidden => "forbidden",
            Self::Credential => "credential",
        }
    }
}

impl Stop {
    /// Whether the stop ends this run's landing to the whole destination,
    /// not only the conversation.
    pub fn destination(&self) -> bool {
        !matches!(self, Self::Conversation(_))
    }

    pub fn message(&self) -> &str {
        match self {
            Self::Conversation(message)
            | Self::Retry { message, .. }
            | Self::Wait { message, .. }
            | Self::Block { message, .. } => message,
        }
    }
}

fn failing(message: String) -> Action {
    Action::Keep(Stop::Wait {
        seconds: WAIT_SECONDS,
        failing: true,
        message,
    })
}

fn block(block: Block, message: String) -> Action {
    Action::Keep(Stop::Block {
        block,
        message,
        status: None,
        code: None,
    })
}

/// A block by a refusal, with its status and code.
fn refused_by(block: Block, refusal: &Refusal, message: String) -> Action {
    Action::Keep(Stop::Block {
        block,
        message,
        status: Some(refusal.status),
        code: refusal.code.clone(),
    })
}

/// What a handshake says when it refuses the credential (`remote/mod.rs`),
/// and what a credential that cannot be read says (`login/local.rs`,
/// `sink.rs`): the only marks a store that cannot be opened leaves.
const CREDENTIAL_REFUSED: [&str; 4] = [
    "rejected the bearer token",
    "rejected this machine credential",
    "run neosian login",
    "requires a credential",
];

/// The stop for a store that cannot be opened: blocked as a 401 when its
/// handshake refused the credential or none could be read for it, else
/// waiting as after a fault.
pub fn unopened(error: &Error) -> Stop {
    let message = error.text();
    let credential = matches!(error, Error::Memory(_))
        && CREDENTIAL_REFUSED.iter().any(|mark| message.contains(mark));
    match credential {
        true => Stop::Block {
            block: Block::Credential,
            message,
            status: None,
            code: None,
        },
        false => Stop::Wait {
            seconds: WAIT_SECONDS,
            failing: true,
            message,
        },
    }
}

/// A held turn's reason: the refusal's status, code and message, or what
/// the person is told when it came without a message.
fn reason(reason: &'static str, refusal: &Refusal, said: String) -> Reason {
    let message = match refusal.message.is_empty() {
        true => said,
        false => refusal.message.clone(),
    };
    Reason::answered(reason, Some(refusal.status), refusal.code.clone(), message)
}

/// Whether a format refusal is about the turn's own draft, which every
/// request names alone: its state, records file or sealed header, or a
/// repeat check of its records (`store/records/drafts.rs`,
/// `postgres/record_write.rs`), not a stored turn of the conversation
/// (`turn N: ...`).
fn own_draft(refusal: &Refusal) -> bool {
    let reason = refusal.details.get("reason").and_then(Value::as_str);
    let reason = reason.unwrap_or_default();
    reason.contains("draft's")
        || reason.starts_with("record ")
        || reason.starts_with("records line ")
        || reason.starts_with("the requested range of stored records")
}

fn written(written: &Written) -> Action {
    match (written.state, &written.turn) {
        (DraftState::Sealed, Some(header)) => Action::Stored(header.clone()),
        (DraftState::Sealed, None) => failing(format!(
            "draft {} answered sealed without its turn",
            written.draft
        )),
        (state, _) => Action::Took(state, written.next_seq),
    }
}

/// The row of 17.4 (or of D10) for `answer` to a request on `route`.
pub fn decide(route: Route, answer: &Answer) -> Action {
    let refusal = match answer {
        Answer::Written(answered) => return written(answered),
        Answer::Refused(refusal) => refusal,
        Answer::Lost(_) | Answer::Unreadable(_) => return failing(said(answer)),
        Answer::Unasked { credential, .. } if *credential => {
            return block(Block::Credential, said(answer));
        }
        Answer::Unasked { .. } => return failing(said(answer)),
        Answer::Unsupported => return Action::Whole,
    };
    let said = said(answer);
    let code = refusal.code.as_deref();
    let conflict = match code {
        Some(CONFLICT) => refusal.details.get("reason").and_then(Value::as_str),
        _ => None,
    };
    match (refusal.status, code, conflict) {
        (429, ..) => match refusal.retry_after {
            Some(seconds) if seconds <= RETRY_SECONDS => Action::Keep(Stop::Retry {
                seconds,
                message: said,
            }),
            seconds => Action::Keep(Stop::Wait {
                seconds: seconds.unwrap_or(WAIT_SECONDS),
                failing: false,
                message: said,
            }),
        },
        (413, Some(STORAGE), _) => refused_by(Block::Storage, refusal, said),
        (401, ..) => refused_by(Block::Credential, refusal, said),
        (403, ..) | (300..=399, ..) => refused_by(Block::Forbidden, refusal, said),
        (_, _, Some("draft_sealed")) => match refusal.details.get("turn").map(Header::from_json) {
            Some(Ok(header)) => Action::Stored(header),
            _ => Action::Probe,
        },
        (_, _, Some("draft_mismatch" | "draft_size")) => {
            Action::Fresh(reason(MISMATCH, refusal, said))
        }
        (400, Some(FORMAT), _) if route == Route::Records && own_draft(refusal) => {
            Action::Fresh(reason(MISMATCH, refusal, said))
        }
        (_, _, Some("draft_gap")) => Action::Restart,
        (_, _, Some("drafts_open")) => Action::Keep(Stop::Conversation(said)),
        (_, _, Some("draft_actor")) => Action::HoldAside(reason(ACTOR_CHANGED, refusal, said)),
        (410, ..) if route == Route::Records => Action::Restart,
        (404, ..) if route == Route::Records => Action::Whole,
        (404, ..) => refused_by(Block::Forbidden, refusal, said),
        (400, Some(CONFIGURATION), _) if route == Route::Records => Action::Reread(said),
        (400..=499, ..) => Action::Hold(reason(REFUSED, refusal, said)),
        _ => failing(said),
    }
}
