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
/// then the trail acts. It holds nothing but the running hash, which a
/// store keeps between parts as hex (T1b2b1).
#[derive(Clone)]
pub struct UnitDigest(Sha256);

impl std::fmt::Debug for UnitDigest {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("UnitDigest")
    }
}

impl serde::Serialize for UnitDigest {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use sha2::digest::common::hazmat::SerializableState;
        let state = self.0.serialize();
        let hex: String = state.iter().map(|byte| format!("{byte:02x}")).collect();
        serializer.serialize_str(&hex)
    }
}

impl<'de> serde::Deserialize<'de> for UnitDigest {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        use serde::de::Error;
        use sha2::digest::common::hazmat::SerializableState;
        let hex = String::deserialize(deserializer)?;
        let bytes: Option<Vec<u8>> = (0..hex.len())
            .step_by(2)
            .map(|at| {
                hex.get(at..at + 2)
                    .and_then(|pair| u8::from_str_radix(pair, 16).ok())
            })
            .collect();
        let state = bytes
            .and_then(|bytes| bytes.as_slice().try_into().ok())
            .ok_or_else(|| D::Error::custom("a unit digest state of the wrong form"))?;
        Sha256::deserialize(&state)
            .map(Self)
            .map_err(|_| D::Error::custom("a unit digest state that does not decode"))
    }
}

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
