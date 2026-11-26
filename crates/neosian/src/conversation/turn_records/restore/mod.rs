//! A restore in parts as every store takes it (DESIGN §40.54 item 12,
//! T1b2b1): the request and its own shape, its items in stream order, the
//! position a probe answers, the answer in its key order, and the restore
//! id the binary derives (item 22). One stream per restore: each turn's
//! header, then its records in `seq` order; then the projections; then the
//! trail acts. The stream's rules are in `stream.rs`; the commit is T1b2b2's.

mod receipt;
mod stream;

pub(crate) use receipt::RestoreReceipt;
pub use stream::{Placed, RestoreStream, StreamRefusal};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{Header, TurnRecord, check_id};
use crate::conversation::rows::render_redaction;
use crate::conversation::rows::{parse_projection, parse_redaction, render_projection};
use crate::conversation::{Projection, Redaction};

/// The most items one request carries (§40.54 item 8.2).
pub const PART_ITEMS: usize = 2_048;

/// One `restore_records` request.
#[derive(Clone, Debug, Default)]
pub struct Restore {
    pub restore: String,
    pub turns: Vec<Header>,
    /// Records in restore shape: each with its turn.
    pub records: Vec<(u64, TurnRecord)>,
    pub projections_from: Option<u64>,
    pub projections: Vec<Projection>,
    pub redactions_from: Option<u64>,
    pub redactions: Vec<Redaction>,
    pub commit: bool,
    pub discard: bool,
}

/// One item of a part, in stream order, with its place where the request
/// gives it (a projection's or an act's index).
#[derive(Debug)]
pub enum Item<'a> {
    Header(&'a Header),
    Record(u64, &'a TurnRecord),
    Projection(u64, &'a Projection),
    Act(u64, &'a Redaction),
}

impl Restore {
    /// A request with no content and neither commit nor discard: a probe.
    pub fn is_probe(&self) -> bool {
        !self.commit && !self.discard && self.items_count() == 0
    }

    /// Whether an empty list is placed past what the restore holds: its
    /// `_from` is checked like the index of a first entry (ruling R28).
    pub fn skips(&self, at: Position) -> bool {
        let past = |from: Option<u64>, empty: bool, held| empty && from > Some(held);
        past(
            self.projections_from,
            self.projections.is_empty(),
            at.projections,
        ) || past(
            self.redactions_from,
            self.redactions.is_empty(),
            at.redactions,
        )
    }

    pub(crate) fn items_count(&self) -> usize {
        self.turns.len() + self.records.len() + self.projections.len() + self.redactions.len()
    }

    /// The request's own shape, before any staged restore is read; every
    /// client number taken with checks.
    pub fn check(&self, conversation_id: &str) -> Result<(), String> {
        check_id("restore", &self.restore)?;
        if self.discard && (self.commit || self.items_count() > 0) {
            return Err("a discard carries no content and no commit".into());
        }
        if self.items_count() > PART_ITEMS {
            return Err(format!("a part carries at most {PART_ITEMS} items"));
        }
        if let Some(header) = self.turns.iter().find(|h| h.turn == 0) {
            return Err(format!("turn {} must be at least 1", header.turn));
        }
        if !self
            .turns
            .windows(2)
            .all(|pair| pair[0].turn < pair[1].turn)
        {
            return Err("a part's turns are ascending".into());
        }
        for header in &self.turns {
            let reason = match (header.redacted, header.records, header.bytes) {
                (true, 0, 0) => continue,
                (true, _, _) => "a redacted turn has no records",
                (false, 0, 0) => "an unredacted turn must have at least one record",
                (false, 0, _) => "a turn without records holds no bytes",
                _ => continue,
            };
            return Err(format!("turn {}: {reason}", header.turn));
        }
        let ordered = self.records.windows(2).all(|pair| {
            let ((a, ra), (b, rb)) = (&pair[0], &pair[1]);
            if a == b {
                ra.seq.checked_add(1) == Some(rb.seq)
            } else {
                a < b && rb.seq == 0
            }
        });
        if !ordered {
            return Err("a part's records follow their turns in seq order".into());
        }
        let places = |from: Option<u64>, count: usize, what: &str| -> Result<(), String> {
            match from {
                None if count > 0 => Err(format!("{what} need {what}_from")),
                Some(from) if from.checked_add(count as u64).is_none() => {
                    Err(format!("{what}_from {from} is past the stream's end"))
                }
                _ => Ok(()),
            }
        };
        places(self.projections_from, self.projections.len(), "projections")?;
        places(self.redactions_from, self.redactions.len(), "redactions")?;
        for entry in &self.projections {
            parse_projection(render_projection(entry).trim_end(), 0, conversation_id)
                .map_err(|_| format!("projection of turn {} has invalid fields", entry.turn))?;
        }
        for act in &self.redactions {
            if act.conversation_id != conversation_id {
                return Err(format!(
                    "a trail act names conversation {:?}, not this one",
                    act.conversation_id
                ));
            }
            parse_redaction(render_redaction(act).trim_end(), 0, conversation_id)
                .map_err(|_| "a trail act has invalid fields".to_string())?;
        }
        Ok(())
    }

    /// The part's items in stream order: headers and records by turn, a
    /// header before its turn's records; then projections; then acts.
    /// Call after `check`.
    pub fn items(&self) -> Vec<Item<'_>> {
        let mut items = Vec::with_capacity(self.items_count());
        let mut records = self.records.iter().peekable();
        for header in &self.turns {
            while let Some((turn, record)) = records.next_if(|(turn, _)| *turn < header.turn) {
                items.push(Item::Record(*turn, record));
            }
            items.push(Item::Header(header));
        }
        items.extend(records.map(|(turn, record)| Item::Record(*turn, record)));
        let from = self.projections_from.unwrap_or(0);
        let entries = self.projections.iter().enumerate();
        items.extend(entries.map(|(at, entry)| Item::Projection(from + at as u64, entry)));
        let from = self.redactions_from.unwrap_or(0);
        let acts = self.redactions.iter().enumerate();
        items.extend(acts.map(|(at, act)| Item::Act(from + at as u64, act)));
        items
    }
}

/// Where a restore stands (item 12): the last header received (0 before
/// any), its records received, whether that turn has all of them, and the
/// projections and acts received.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Position {
    pub turn: u64,
    pub seq: u64,
    pub complete: bool,
    pub projections: u64,
    pub redactions: u64,
}

impl Default for Position {
    fn default() -> Self {
        Self {
            turn: 0,
            seq: 0,
            complete: true,
            projections: 0,
            redactions: 0,
        }
    }
}

impl Position {
    pub fn to_json(self) -> Value {
        json!({
            "turn": self.turn,
            "seq": self.seq,
            "complete": self.complete,
            "projections": self.projections,
            "redactions": self.redactions,
        })
    }
}

/// Where a restore stands after a request.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Staged {
    None,
    Open,
    /// Answered by the commit (T1b2b2).
    #[cfg_attr(test, allow(dead_code))]
    Committed,
    Discarded,
}

impl Staged {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Open => "open",
            Self::Committed => "committed",
            Self::Discarded => "discarded",
        }
    }
}

/// The answer to one `restore_records` request.
#[derive(Debug, PartialEq, Eq)]
pub struct Restored {
    pub restore: String,
    pub state: Staged,
    pub position: Option<Position>,
    pub stored_bytes: u64,
    pub freed_bytes: u64,
    pub digest: Option<String>,
}

impl Restored {
    pub(crate) fn new(restore: &str, state: Staged, position: Option<Position>) -> Self {
        Self {
            restore: restore.to_string(),
            state,
            position,
            stored_bytes: 0,
            freed_bytes: 0,
            digest: None,
        }
    }

    /// The answer in item 12's key order.
    pub fn to_json(&self) -> Value {
        json!({
            "restore": self.restore,
            "state": self.state.as_str(),
            "position": self.position.map(Position::to_json),
            "stored_bytes": self.stored_bytes,
            "freed_bytes": self.freed_bytes,
            "digest": self.digest,
        })
    }
}

/// The restore id the binary uses for a conversation (item 22): `i` and the
/// first 32 hex digits of the SHA-256 of its id, so a rerun probes and
/// resumes. A store takes any valid id. The import calls it (T1e).
#[cfg_attr(not(test), allow(dead_code))]
pub fn restore_id(conversation_id: &str) -> String {
    let digest = Sha256::digest(conversation_id.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("i{}", &hex[..32])
}
