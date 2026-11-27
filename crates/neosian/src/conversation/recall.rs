//! `recall_turn` and `neosian recall` (DESIGN §33, §40.54 items 15.2 and
//! 19, #448): one turn of a conversation in a view, `light` by default,
//! or one part of one piece of it. A store that keeps records is read
//! with views (`read_records`), only the kinds and heads a view needs, a
//! page at a time. Any other store's turn is read whole as before, every
//! turn visited and only the one shown kept (§40.46), then converted to
//! the same records in memory and shown by the same rules. Only the
//! capability's absence selects the whole read: a records store's refusal
//! is answered as a refusal, never retried on the legacy routes.

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

use self::origin::{Item, Origin};
use super::turn_records::{Header, Kind, RecordRead, records_of};
use super::{Error, Turn};
use crate::assets::{TOOLS, mapping, quoted, render};
use crate::remote::records::Failure;
use crate::state::State;
use crate::state::records::{Fault, Records};

/// The code a store refuses a whole turn with when records cannot carry it.
const UNCONVERTIBLE: &str = "agent_conversation_format_unsupported";

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
/// records; `label` names the conversation in a corrective hint. A whole
/// turn records cannot carry is the one refusal that falls back to the
/// whole read; every other refusal is answered.
pub fn recall(
    store: &dyn State,
    records: Option<&dyn Records>,
    ask: &Ask,
    label: &str,
) -> Result<Result<Recalled, Corrective>, Error> {
    if let Some(records) = records {
        let conversation = &ask.conversation;
        match shown(
            &Origin::Kept {
                records,
                conversation,
            },
            ask,
            label,
        ) {
            Err(fault) if unconvertible(&fault) => {}
            Err(fault) => return refusal(fault).map(Err),
            Ok(answer) => return Ok(answer),
        }
    }
    match whole(store, &ask.conversation, ask.turn, label)? {
        Ok(Whole::Records(origin)) => shown(&origin, ask, label).or_else(|f| refusal(f).map(Err)),
        Ok(Whole::Released(turn)) => Ok(released::answer(&turn, ask)),
        Err(corrective) => Ok(Err(corrective)),
    }
}

fn shown(origin: &Origin, ask: &Ask, label: &str) -> Result<Result<Recalled, Corrective>, Fault> {
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
        Outcome::Missing => Err(missing(ask.turn, label, last_turn(origin)?)),
    })
}

/// A store's refusal of a whole turn its records cannot carry (§40.54
/// item 10).
pub(super) fn unconvertible(fault: &Fault) -> bool {
    let code = match fault {
        Fault::Local(Error::Conversation(refused)) => Some(refused.code),
        Fault::Remote(Failure::Refused(refused)) => refused.code.as_deref(),
        _ => None,
    };
    code == Some(UNCONVERTIBLE)
}

fn missing(turn: u64, label: &str, last: u64) -> Corrective {
    Corrective::new(
        format!("Turn {turn} does not exist"),
        Some(format!("{label} has turns 1-{last}.")),
    )
}

/// The conversation's last turn, from its headers alone.
fn last_turn(origin: &Origin) -> Result<u64, Fault> {
    let read = RecordRead {
        kinds: Some(Vec::new()),
        ..RecordRead::default()
    };
    let mut last = 0;
    origin.read(&read, &mut |item| {
        if let Item::Header(header) = item {
            last = last.max(header.turn);
        }
        Ok(true)
    })?;
    Ok(last)
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

/// What the whole read found: the turn's records, or the turn itself
/// when records cannot carry it.
enum Whole {
    Records(Origin<'static>),
    Released(Turn),
}

/// The turn read whole, as before (every turn read, only the one shown
/// kept), and converted to its records; or the corrective failure.
fn whole(
    store: &dyn State,
    target: &str,
    turn: u64,
    label: &str,
) -> Result<Result<Whole, Corrective>, Error> {
    let (mut kept, mut last) = (None, 0);
    let read = store.read_turns_each(target, 0, None, &mut |found| {
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
    let Some(found) = kept else {
        return Ok(Err(missing(turn, label, last)));
    };
    let Ok(records) = records_of(&found.messages) else {
        return Ok(Ok(Whole::Released(found)));
    };
    let mut header = Header::of(found.turn, found.created_at, found.actor, &records);
    header.redacted = found.redacted;
    Ok(Ok(Whole::Records(Origin::Whole { header, records })))
}
