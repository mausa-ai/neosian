//! `recall_turn` and `neosian recall` (DESIGN §33, §40.54 items 15.2 and
//! 19, #448): one turn of a conversation in a view, `light` by default,
//! or one part of one piece of it. A store that keeps records is read
//! with views (`read_records`), only the kinds and heads a view needs, a
//! page at a time. Any other store's turn is read whole as before, every
//! turn visited and only the one shown kept (§40.46), then converted to
//! the same records in memory and shown by the same rules, each turn
//! checked to be of the conversation asked. Only the capability's absence
//! selects the whole read: a records store's refusal is answered as a
//! refusal, except its refusal of the asked turn because records cannot
//! carry it, when that one turn alone is read whole.

mod ask;
mod light;
pub(super) mod origin;
mod part;
pub(super) mod pieces;
mod released;
mod view;

#[cfg(test)]
mod tests;

pub use ask::{Ask, Selector, View};

use self::origin::Origin;
use super::ids::read_of;
use super::turn_records::{Header, Kind, records_of};
use super::{Error, Turn};
use crate::assets::{TOOLS, mapping, quoted, render};
use crate::remote::records::Failure;
use crate::state::State;
use crate::state::records::{Fault, Records};

/// The code a store refuses a whole turn with when records cannot carry it.
const UNCONVERTIBLE: &str = "agent_conversation_format_unsupported";
/// How the conversion's reason ends (`turn_records/convert.rs`).
const CANNOT_CARRY: &str = ", which records cannot carry";

/// A corrective failure as the door answers it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Corrective {
    pub error: String,
    pub hint: Option<String>,
}

impl Corrective {
    pub fn new(error: String, hint: Option<String>) -> Self {
        Self { error, hint }
    }
}

/// What a recall shows: the text, and whether it is a whole turn's view,
/// which the MCP door annotates with the mailbox; a part is not.
#[derive(Debug, PartialEq, Eq)]
pub struct Recalled {
    pub text: String,
    pub view: bool,
}

/// A view's or a part's answer before the door frames it.
enum Outcome {
    Shown(String),
    Refused(Corrective),
    /// The turn is not in the conversation.
    Missing,
}

fn hint(key: &str) -> String {
    quoted(TOOLS, key).expect("tools.yaml carries the recall hints")
}

fn marker(key: &str, variables: &[(&str, &str)]) -> String {
    let template = quoted(TOOLS, key).expect("tools.yaml carries the recall markers");
    render(&template, variables)
}

/// A byte count with thousands separators: `812,345`.
pub(super) fn grouped(number: u64) -> String {
    let digits = number.to_string();
    let mut out = String::new();
    for (at, digit) in digits.chars().enumerate() {
        if at > 0 && (digits.len() - at).is_multiple_of(3) {
            out.push(',');
        }
        out.push(digit);
    }
    out
}

/// A part header's words: nothing for part 0, `, part 3` after it.
fn part_of(part: u64) -> String {
    if part == 0 {
        String::new()
    } else {
        marker("recall_turn_part", &[("part", &part.to_string())])
    }
}

/// What a marker calls a piece of `kind`: `a result`.
fn piece_noun(kind: Kind) -> String {
    let nouns = mapping(TOOLS, "recall_turn_pieces").expect("tools.yaml carries the nouns");
    nouns
        .into_iter()
        .find(|(name, _)| name == kind.as_str())
        .map_or_else(|| "a piece".to_string(), |(_, noun)| noun)
}

/// `ask` answered from `store`, through `records` when the store keeps
/// records; `label` names the conversation in a corrective hint. A store's
/// refusal of the asked turn because records cannot carry it is the one
/// refusal answered otherwise: that turn alone is read whole. Every other
/// refusal is answered, a damaged record's included.
pub fn recall(
    store: &dyn State,
    records: Option<&dyn Records>,
    ask: &Ask,
    label: &str,
) -> Result<Result<Recalled, Corrective>, Error> {
    if let Some(records) = records {
        let conversation = &ask.conversation;
        let origin = Origin::Kept {
            records,
            conversation,
        };
        let last = || Ok(store.last_turn_number(conversation)?);
        return match shown(&origin, ask, label, &last) {
            Err(fault) if conversion(&fault) == Some(ask.turn) => one(store, ask, label),
            Err(fault) => refusal(fault).map(Err),
            Ok(answer) => Ok(answer),
        };
    }
    match whole(store, &ask.conversation, ask.turn, label)? {
        Ok(found) => answered(found, ask, label),
        Err(corrective) => Ok(Err(corrective)),
    }
}

/// The turn found whole, shown from its records, or as released when
/// records cannot carry it.
fn answered(found: Turn, ask: &Ask, label: &str) -> Result<Result<Recalled, Corrective>, Error> {
    let Ok(records) = records_of(&found.messages) else {
        return Ok(released::answer(&found, ask));
    };
    let mut header = Header::of(found.turn, found.created_at, found.actor, &records);
    header.redacted = found.redacted;
    let turn = header.turn;
    let origin = Origin::Whole { header, records };
    shown(&origin, ask, label, &|| Ok(turn)).or_else(|fault| refusal(fault).map(Err))
}

/// The view or part `ask` names, from `origin`; a turn it does not hold is
/// missing, with `last` the conversation's last turn for the hint.
fn shown(
    origin: &Origin,
    ask: &Ask,
    label: &str,
    last: &dyn Fn() -> Result<u64, Fault>,
) -> Result<Result<Recalled, Corrective>, Fault> {
    let shown = match &ask.part {
        None => view::render(origin, ask.turn, ask.view)?,
        Some((selector, part)) => part::render(origin, ask.turn, selector, *part)?,
    };
    Ok(match shown {
        Outcome::Shown(text) => Ok(Recalled {
            text,
            view: ask.part.is_none(),
        }),
        Outcome::Refused(corrective) => Err(corrective),
        Outcome::Missing => Err(missing(ask.turn, label, last()?)),
    })
}

/// A store's refusal of a whole turn its records cannot carry (§40.54
/// item 10), whatever the reason: the code alone.
pub(super) fn unconvertible(fault: &Fault) -> bool {
    refused(fault).is_some_and(|(code, _)| code == UNCONVERTIBLE)
}

/// The turn a store refused because records cannot carry it: the code,
/// and the reason the conversion gives, `turn N: message M has a ...,
/// which records cannot carry` (§40.54 item 10). The same code also
/// answers a damaged record, whose reason names no such message, so that
/// refusal is told apart and stays a refusal.
pub(crate) fn conversion(fault: &Fault) -> Option<u64> {
    let (code, reason) = refused(fault)?;
    if code != UNCONVERTIBLE {
        return None;
    }
    let reason = reason.strip_suffix(CANNOT_CARRY)?;
    let at = reason.rfind("turn ")?;
    let (number, said) = reason[at + "turn ".len()..].split_once(": message ")?;
    if !said.contains(" has a ") {
        return None;
    }
    number.parse().ok().filter(|turn| *turn > 0)
}

/// A refusal's code and its reason: the details' `reason` when they carry
/// one, else the message.
fn refused(fault: &Fault) -> Option<(&str, &str)> {
    let (code, message, details) = match fault {
        Fault::Local(Error::Conversation(refused)) => {
            (refused.code, refused.message.as_str(), &refused.details)
        }
        Fault::Remote(Failure::Refused(refused)) => (
            refused.code.as_deref()?,
            refused.message.as_str(),
            &refused.details,
        ),
        _ => return None,
    };
    let reason = details.get("reason").and_then(|reason| reason.as_str());
    Some((code, reason.unwrap_or(message)))
}

fn missing(turn: u64, label: &str, last: u64) -> Corrective {
    Corrective::new(
        format!("Turn {turn} does not exist"),
        Some(format!("{label} has turns 1-{last}.")),
    )
}

/// A refusal as the door answers it, in the tier the whole read answers
/// it in: a local store's conversation refusal is the corrective `[code]
/// message`; a server's refusal is the tool's failure, `[code] message`
/// as its envelope says; anything else is the tool's failure.
fn refusal(fault: Fault) -> Result<Corrective, Error> {
    match fault {
        Fault::Local(Error::Conversation(refused)) => Ok(Corrective::new(
            format!("[{}] {}", refused.code, refused.message),
            None,
        )),
        Fault::Remote(Failure::Refused(refused)) if refused.code.is_some() => {
            let code = refused.code.as_deref().unwrap_or_default();
            Err(Error::Value(format!("[{code}] {}", refused.message)))
        }
        fault => Err(fault.into_error()),
    }
}

/// The turn read whole from a store without records, as before (every
/// turn read, only the one shown kept), or the corrective failure.
fn whole(
    store: &dyn State,
    target: &str,
    turn: u64,
    label: &str,
) -> Result<Result<Turn, Corrective>, Error> {
    let (mut kept, mut last) = (None, 0);
    let read = store.read_turns_each(target, 0, None, &mut |found| {
        read_of(&found, target)?;
        last = found.turn;
        if kept.is_none() && found.turn == turn {
            kept = Some(found);
        }
        Ok(true)
    });
    match read {
        Ok(()) => {}
        Err(Error::Conversation(refused)) => {
            let error = format!("[{}] {}", refused.code, refused.message);
            return Ok(Err(Corrective::new(error, None)));
        }
        Err(error) => return Err(error),
    }
    Ok(kept.ok_or_else(|| missing(turn, label, last)))
}

/// The asked turn alone, read whole from a store that keeps records after
/// it refused that turn because records cannot carry it: no other turn is
/// read.
fn one(store: &dyn State, ask: &Ask, label: &str) -> Result<Result<Recalled, Corrective>, Error> {
    let (target, turn) = (ask.conversation.as_str(), ask.turn);
    let mut kept = None;
    let after = i64::try_from(turn.saturating_sub(1)).unwrap_or(i64::MAX);
    store.read_turns_each(target, after, Some(1), &mut |found| {
        read_of(&found, target)?;
        kept = (found.turn == turn).then_some(found);
        Ok(false)
    })?;
    match kept {
        Some(found) => answered(found, ask, label),
        None => {
            let last = store.last_turn_number(target)?;
            Ok(Err(missing(turn, label, last)))
        }
    }
}
