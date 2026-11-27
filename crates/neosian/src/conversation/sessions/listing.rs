//! The landing facts a sessions document lists (DESIGN §40.54 item 19,
//! #448; the T1fd slice and the plan's listing accounting): `recorded`,
//! the kinds of the records landed, in a fixed order, and `size`, their
//! bytes as the sealed headers count them. Both are cumulative from the
//! first landing the document represents and carried from the version the
//! landing read, so no turn is read again. A landing sent whole has no
//! sealed header and lists no pair; a duplicate receipt adds nothing; a gap
//! in the turn numbers, a hand-edited pair or a stale landing leaves the
//! pair out of that write, and the next landing that follows its document
//! directly starts the count again, its first turn named on the size line.
//! An erasure that covers the counted turns removes the pair.

use super::{pairs, sessions_path};
use crate::conversation::Error;
use crate::conversation::turn_records::{Kind, TurnRecord};
use crate::state::State;
use crate::store::grammar::Actor;

/// The order the `recorded` line names kinds in.
const ORDER: [Kind; 8] = [
    Kind::Prompt,
    Kind::Reply,
    Kind::ToolCall,
    Kind::ToolResult,
    Kind::System,
    Kind::Reasoning,
    Kind::Attachment,
    Kind::Extra,
];
const RECORDED: &str = "recorded";
const SIZE: &str = "size";

/// A set of record kinds.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Kinds(u8);

impl Kinds {
    /// The kinds of the records a landing packed.
    pub fn of(records: &[TurnRecord]) -> Self {
        Self(records.iter().fold(0, |set, record| set | bit(record.kind)))
    }

    fn names(self) -> Vec<&'static str> {
        ORDER
            .into_iter()
            .filter(|kind| self.0 & bit(*kind) != 0)
            .map(Kind::as_str)
            .collect()
    }
}

fn bit(kind: Kind) -> u8 {
    1 << ORDER.iter().position(|known| *known == kind).unwrap_or(0)
}

/// What one sealed landing adds: its records' kinds and the bytes its
/// sealed header counts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Landed {
    pub kinds: Kinds,
    pub bytes: u64,
}

/// The pair a document lists: the kinds and bytes counted from turn
/// `since` through its `turns` line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Count {
    kinds: Kinds,
    bytes: u64,
    since: u64,
}

impl Count {
    fn start(turn: u64, landed: &Landed) -> Option<Self> {
        (landed.kinds.0 != 0).then_some(Self {
            kinds: landed.kinds,
            bytes: landed.bytes,
            since: turn.max(1),
        })
    }

    fn add(self, landed: &Landed) -> Option<Self> {
        Some(Self {
            kinds: Kinds(self.kinds.0 | landed.kinds.0),
            bytes: self.bytes.checked_add(landed.bytes)?,
            since: self.since,
        })
    }

    /// The two lines as the document carries them.
    pub fn lines(&self) -> String {
        let since = match self.since {
            1 => String::new(),
            turn => format!(" since turn {turn}"),
        };
        format!(
            "- {RECORDED}: {}\n- {SIZE}: {} bytes{since}\n",
            self.kinds.names().join(", "),
            self.bytes
        )
    }
}

/// What a document says about the pair.
enum Prior {
    Absent,
    Listed(Count),
    Malformed,
}

/// The `turns` line of a document, the last one as the document reads.
fn turns_of(content: &str) -> Option<u64> {
    let value = pairs(content)
        .into_iter()
        .rev()
        .find(|(key, _)| *key == "turns")?
        .1;
    value.parse::<u64>().ok().filter(|n| n.to_string() == value)
}

/// The pair as `content` lists it through turn `turns`: well formed only
/// when each line appears once and renders back exactly.
fn prior(content: &str, turns: u64) -> Prior {
    let fields = pairs(content);
    let values = |name: &str| -> Vec<&str> {
        fields
            .iter()
            .filter(|(key, _)| *key == name)
            .map(|(_, value)| *value)
            .collect()
    };
    let (recorded, size) = (values(RECORDED), values(SIZE));
    match (recorded.as_slice(), size.as_slice()) {
        ([], []) => Prior::Absent,
        ([recorded], [size]) => {
            parsed(recorded, size, turns).map_or(Prior::Malformed, Prior::Listed)
        }
        _ => Prior::Malformed,
    }
}

fn parsed(recorded: &str, size: &str, turns: u64) -> Option<Count> {
    let mut kinds = Kinds::default();
    for name in recorded.split(", ") {
        kinds.0 |= bit(Kind::parse(name)?);
    }
    let (bytes, since) = match size.split_once(" bytes") {
        Some((bytes, "")) => (bytes, 1),
        Some((bytes, rest)) => (bytes, rest.strip_prefix(" since turn ")?.parse().ok()?),
        None => return None,
    };
    let count = Count {
        kinds,
        bytes: bytes.parse().ok()?,
        since,
    };
    let lines = format!("- {RECORDED}: {recorded}\n- {SIZE}: {size}\n");
    (count.lines() == lines && (1..=turns).contains(&since)).then_some(count)
}

/// The pair the document for landing `turn` lists, from the version read
/// (`listed`, None when there is none or it is erased) and what the
/// landing adds (`landed`, None for a turn sent whole).
pub fn next(listed: Option<&str>, turn: u64, landed: Option<&Landed>) -> Option<Count> {
    let landed = landed?;
    let Some(content) = listed else {
        return Count::start(turn, landed);
    };
    let last = turns_of(content)?;
    match prior(content, last) {
        Prior::Malformed => None,
        // A duplicate receipt adds nothing.
        Prior::Listed(count) if last == turn => Some(count),
        Prior::Listed(count) if last.checked_add(1) == Some(turn) => count.add(landed),
        Prior::Absent if last.checked_add(1) == Some(turn) => Count::start(turn, landed),
        // A gap, a stale landing, or a duplicate of one that listed none.
        _ => None,
    }
}

/// Which turns an erasure removed: 1 through N, or the ones named.
pub enum Erased<'a> {
    Through(u64),
    Turns(&'a [i64]),
}

/// `content` without its pair when `erased` covers a turn it counts, or
/// when the pair cannot be read; None when nothing is to change.
pub fn cleared(content: &str, erased: &Erased) -> Option<String> {
    let fields = pairs(content);
    if !fields
        .iter()
        .any(|(key, _)| *key == RECORDED || *key == SIZE)
    {
        return None;
    }
    let counted = turns_of(content).and_then(|turns| match prior(content, turns) {
        Prior::Listed(count) => Some((count.since, turns)),
        _ => None,
    });
    let covers = counted.is_none_or(|(since, turns)| match erased {
        Erased::Through(through) => *through >= since,
        Erased::Turns(numbers) => numbers
            .iter()
            .any(|n| u64::try_from(*n).is_ok_and(|n| (since..=turns).contains(&n))),
    });
    covers.then(|| {
        content
            .split_inclusive('\n')
            .filter(|line| {
                !line.starts_with(&format!("- {RECORDED}: "))
                    && !line.starts_with(&format!("- {SIZE}: "))
            })
            .collect()
    })
}

/// The conversation's sessions document in every scope without the pair
/// `erased` covers, written by `actor` against the version read.
pub fn clear(
    store: &dyn State,
    conversation: &str,
    erased: &Erased,
    actor: &Actor,
) -> Result<(), Error> {
    let path = sessions_path(conversation);
    for scope in store.scopes()? {
        let Some(document) = store.read(&scope, &path)? else {
            continue;
        };
        if document.envelope.redacted {
            continue;
        }
        if let Some(content) = cleared(&document.content, erased) {
            let version = Some(document.envelope.version);
            store.write(&scope, &path, &content, actor, version)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests;
