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
use crate::store::time::{Stamp, Unreadable};
use crate::wire::encode;

/// A value of a request as a refusal repeats it: its first 128 scalars
/// (ruling R18), so a refusal does not grow with the body that earned it.
pub fn cut(text: &str) -> String {
    text.chars().take(128).collect()
}

/// Why a restore refuses a time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeRefusal {
    Malformed,
    Naive,
    /// More than six fractional digits: nothing is dropped silently.
    Fraction,
    /// Its UTC form falls outside years 0000 to 9999.
    Range,
}

/// The first and one past the last microsecond of years 0000 to 9999.
const YEARS: std::ops::Range<i64> = -62_167_219_200_000_000..253_402_300_800_000_000;

/// A time of a restore (a header's, a trail act's): any offset, kept as
/// its instant, with at most six fractional digits and a UTC form within
/// years 0000 to 9999, so the store writes back exactly what was sent.
pub fn restore_time(text: &str) -> Result<Stamp, TimeRefusal> {
    let bytes = text.as_bytes();
    let digits = |from: usize| bytes.iter().skip(from).take_while(|b| b.is_ascii_digit());
    if bytes.get(19) == Some(&b'.') && digits(20).count() > 6 {
        return Err(TimeRefusal::Fraction);
    }
    let stamp = Stamp::parse(text).map_err(|unreadable| match unreadable {
        Unreadable::Naive => TimeRefusal::Naive,
        Unreadable::Malformed => TimeRefusal::Malformed,
    })?;
    if !YEARS.contains(&stamp.instant()) {
        return Err(TimeRefusal::Range);
    }
    Ok(stamp.utc())
}

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

/// The keys of a header, in §40.54 item 3.6's order.
const HEADER_KEYS: [&str; 6] = [
    "turn",
    "created_at",
    "actor",
    "redacted",
    "records",
    "bytes",
];

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

    /// A header as a restore sends it (§40.54 items 8.2 and 12): an
    /// unknown key and a mistyped value are refused, `actor` alone may be
    /// null or absent, and the time is taken at any offset and kept as its
    /// instant. A refusal repeats at most 128 scalars of a key.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let map = value.as_object().ok_or("a turn header must be an object")?;
        if let Some(key) = map.keys().find(|key| !HEADER_KEYS.contains(&key.as_str())) {
            return Err(format!("unknown header field {:?}", cut(key)));
        }
        let field = |key: &str| {
            let found = map.get(key);
            found.ok_or_else(|| format!("header field {key:?} is required"))
        };
        let number = |key: &str| {
            let found = field(key)?.as_u64();
            found.ok_or_else(|| format!("header field {key:?} must be a non-negative integer"))
        };
        let turn = number("turn")?;
        if turn == 0 {
            return Err("header field \"turn\" must be an integer >= 1".into());
        }
        let created_at = match field("created_at")?.as_str().map(restore_time) {
            Some(Ok(stamp)) => stamp,
            Some(Err(TimeRefusal::Fraction)) => {
                return Err(
                    "header field \"created_at\" has more than six fractional digits".into(),
                );
            }
            Some(Err(TimeRefusal::Range)) => {
                return Err(
                    "header field \"created_at\" is outside years 0000 to 9999 in UTC".into(),
                );
            }
            _ => {
                return Err(
                    "header field \"created_at\" must be an ISO-8601 time with an offset".into(),
                );
            }
        };
        let actor = match map.get("actor") {
            None | Some(Value::Null) => None,
            Some(Value::String(actor)) => Some(actor.clone()),
            Some(_) => return Err("header field \"actor\" must be a string or null".into()),
        };
        let redacted = field("redacted")?
            .as_bool()
            .ok_or("header field \"redacted\" must be a boolean")?;
        Ok(Self {
            turn,
            created_at,
            actor,
            redacted,
            records: number("records")?,
            bytes: number("bytes")?,
        })
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
