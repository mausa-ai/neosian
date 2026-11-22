//! Canonical encodings and digests (§40.54 items 3.6 and 12, with the
//! implementation note): compact JSON, every key in the section's order,
//! null ones included, non-ASCII raw. A record's digest serves idempotent
//! repeats; the unit digest of a restore is SHA-256 over the 32-byte
//! digests of its items in stream order, computed as the stream passes.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::TurnRecord;
use crate::conversation::{Projection, Redaction};
use crate::pyfmt::dumps;
use crate::store::time::Stamp;
use crate::wire::encode;

fn sha256(text: &str) -> [u8; 32] {
    Sha256::digest(text.as_bytes()).into()
}

impl TurnRecord {
    /// The record's canonical encoding, in write shape or, with `turn`,
    /// restore shape.
    pub fn canonical(&self, turn: Option<u64>) -> String {
        dumps(&self.to_json(turn), false)
    }

    pub fn digest(&self, turn: Option<u64>) -> [u8; 32] {
        sha256(&self.canonical(turn))
    }
}

/// A turn's header (§40.54 item 3.6), answered beside its records and
/// sent before them in a restore.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Header {
    pub turn: u64,
    pub created_at: Stamp,
    pub actor: Option<String>,
    pub redacted: bool,
    pub records: u64,
    pub bytes: u64,
}

impl Header {
    /// The header of a turn holding `records`: their count and the UTF-8
    /// bytes of all their texts, which is the sum of `size` over pieces.
    pub fn of(turn: u64, created_at: Stamp, actor: Option<String>, records: &[TurnRecord]) -> Self {
        Self {
            turn,
            created_at,
            actor,
            redacted: false,
            records: records.len() as u64,
            bytes: records.iter().map(|record| record.text.len() as u64).sum(),
        }
    }

    pub fn to_json(&self) -> Value {
        json!({
            "turn": self.turn,
            "created_at": self.created_at.utc().z(),
            "actor": self.actor,
            "redacted": self.redacted,
            "records": self.records,
            "bytes": self.bytes,
        })
    }

    pub fn canonical(&self) -> String {
        dumps(&self.to_json(), false)
    }
}

/// The unit digest of one restore stream, fed item by item in stream
/// order: each turn's header then its records, then the projections,
/// then the trail acts. It holds nothing but the running hash.
pub struct UnitDigest(Sha256);

impl Default for UnitDigest {
    fn default() -> Self {
        Self(Sha256::new())
    }
}

impl UnitDigest {
    pub fn header(&mut self, header: &Header) {
        self.0.update(sha256(&header.canonical()));
    }

    pub fn record(&mut self, turn: u64, record: &TurnRecord) {
        self.0.update(record.digest(Some(turn)));
    }

    pub fn projection(&mut self, entry: &Projection) {
        self.0
            .update(sha256(&dumps(&encode::projection(entry), false)));
    }

    pub fn act(&mut self, act: &Redaction) {
        self.0
            .update(sha256(&dumps(&encode::turn_redaction(act), false)));
    }

    /// The digest as lowercase hex.
    pub fn finish(self) -> String {
        self.0
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }
}
