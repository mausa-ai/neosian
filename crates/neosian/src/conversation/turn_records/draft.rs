//! A write in parts as every store takes and answers it (§40.54 item 9):
//! the request a draft's part carries, the answer in its key order, the
//! conflicts with their details, and the refusal past the store's bound.

use serde_json::{Value, json};

use super::{Header, TurnRecord};
use crate::conversation::Refused;
use crate::limits::RECORD_TURN_BYTES;

/// One `write_records` request, its actor already stamped by the caller.
#[derive(Debug, Default)]
pub struct Write {
    pub draft: String,
    pub records: Vec<TurnRecord>,
    pub turn_bytes: Option<u64>,
    pub seal: bool,
    pub discard: bool,
    pub actor: Option<String>,
}

/// Where a draft stands after a request.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    None,
    Open,
    Sealed,
    Discarded,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Open => "open",
            Self::Sealed => "sealed",
            Self::Discarded => "discarded",
        }
    }
}

/// The answer to one `write_records` request.
#[derive(Debug, PartialEq, Eq)]
pub struct Written {
    pub draft: String,
    pub state: State,
    pub next_seq: u64,
    pub turn: Option<Header>,
    pub stored_bytes: u64,
    pub freed_bytes: u64,
}

impl Written {
    /// The answer in §40.54's key order.
    pub fn to_json(&self) -> Value {
        json!({
            "draft": self.draft,
            "state": self.state.as_str(),
            "next_seq": self.next_seq,
            "turn": self.turn.as_ref().map(Header::to_json),
            "stored_bytes": self.stored_bytes,
            "freed_bytes": self.freed_bytes,
        })
    }
}

/// A draft or restore id: 1 to 64 of `[A-Za-z0-9_-]`.
pub fn check_draft_id(id: &str) -> Result<(), String> {
    let fits = (1..=64).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-');
    if fits {
        Ok(())
    } else {
        Err(format!("draft id {id:?} must be 1 to 64 of [A-Za-z0-9_-]"))
    }
}

impl Write {
    /// The request's own shape, before any draft is read.
    pub fn check(&self) -> Result<(), String> {
        check_draft_id(&self.draft)?;
        if self.discard && (self.seal || !self.records.is_empty()) {
            return Err("a discard carries no records and no seal".into());
        }
        if self.records.len() > 2_048 {
            return Err("a part carries at most 2048 records".into());
        }
        let consecutive = self
            .records
            .windows(2)
            .all(|pair| pair[0].seq.checked_add(1) == Some(pair[1].seq));
        if !consecutive {
            return Err("a part's records carry consecutive seq".into());
        }
        Ok(())
    }
}

/// `agent_conversation_conflict` with a reason and its details (§40.54
/// item 8.2).
pub fn conflict(conversation_id: &str, reason: &str, details: Value) -> Refused {
    let mut refused = Refused::conflict(conversation_id, reason);
    if let (Value::Object(extra), Some(map)) = (details, refused.details.as_object_mut()) {
        map.extend(extra);
    }
    refused
}

/// The refusal of a turn past `NEOSIAN_MAX_RECORD_TURN_BYTES` (item 8.2).
pub fn size_refusal(bytes: u64, maximum: u64) -> String {
    format!(
        "resource limit {RECORD_TURN_BYTES} exceeded: {bytes} > {maximum}; this store does not keep a turn this large"
    )
}
