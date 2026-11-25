//! Shared selection outcomes and lossless conversion checks for record migration.
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

/// Whole turns a migration left as they were, by reason.
#[derive(Debug, Default, PartialEq, Eq, Serialize)]
pub struct Kept {
    /// The stored representation differs from what the codec writes.
    pub spelling: u64,
    /// The stored content cannot be reproduced by the turn and record codecs.
    pub content: u64,
    /// The turn is over `NEOSIAN_MAX_RECORD_TURN_BYTES`.
    pub size: u64,
    /// The turn's number is already occupied by records.
    pub number: u64,
}

/// What one conversation's migration did and what it found.
#[derive(Debug, Default, PartialEq, Eq, Serialize)]
pub struct Migrated {
    pub conversation_id: String,
    /// Whole turns this run turned into records.
    pub migrated: u64,
    /// Turns that were records already.
    pub records: u64,
    /// Erased turns, which hold nothing to migrate.
    pub skeletons: u64,
    pub kept: Kept,
}

/// What one conversation's fold did and what it found.
#[derive(Debug, Default, PartialEq, Eq, Serialize)]
pub struct Folded {
    pub conversation_id: String,
    /// Turns stored as records that this run turned back into whole turns.
    pub folded: u64,
    /// Turns that were whole already.
    pub whole: u64,
    /// Erased turns, which were whole rows already.
    pub skeletons: u64,
    /// Open drafts `--discard-pending` removed.
    pub drafts: u64,
    /// Staged restores `--discard-pending` removed.
    pub restores: u64,
}

/// The `draft` a migrated turn's header names: `m` and the first 32 hex
/// digits of the SHA-256 of its conversation's id, the restore id's rule
/// under another letter, so a rerun writes the same headers.
pub(crate) fn migration_id(conversation_id: &str) -> String {
    let digest = Sha256::digest(conversation_id.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("m{}", &hex[..32])
}

/// Whether the rendered value holds everything `stored` does:
/// every key with its value, every array whole. A key the stored value
/// leaves to its default is still the same turn in another spelling.
pub(crate) fn carried(stored: &Value, written: &Value) -> bool {
    match (stored, written) {
        (Value::Object(stored), Value::Object(written)) => stored
            .iter()
            .all(|(key, value)| written.get(key).is_some_and(|new| carried(value, new))),
        (Value::Array(stored), Value::Array(written)) => {
            stored.len() == written.len() && stored.iter().zip(written).all(|(a, b)| carried(a, b))
        }
        _ => stored == written,
    }
}
