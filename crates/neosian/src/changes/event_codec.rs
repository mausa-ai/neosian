//! Flattened serde structs otherwise silently discard unknown event fields.

use serde::{Deserialize, Deserializer, de::Error};
use serde_json::Value;

use super::model::{Change, Event};

impl<'de> Deserialize<'de> for Event {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        const FIELDS: [&str; 12] = [
            "format",
            "id",
            "stream",
            "sequence",
            "scope",
            "kind",
            "path",
            "version",
            "previous_path",
            "previous_version",
            "created_at",
            "reason",
        ];
        let value = Value::deserialize(deserializer)?;
        if !value.as_object().is_some_and(|map| {
            map.len() == FIELDS.len() && FIELDS.iter().all(|key| map.contains_key(*key))
        }) {
            return Err(D::Error::custom("invalid change-event fields"));
        }
        #[derive(Deserialize)]
        struct Decoded {
            format: u32,
            id: String,
            stream: String,
            sequence: u64,
            scope: String,
            #[serde(flatten)]
            change: Change,
        }
        let decoded = Decoded::deserialize(value).map_err(D::Error::custom)?;
        Ok(Self {
            format: decoded.format,
            id: decoded.id,
            stream: decoded.stream,
            sequence: decoded.sequence,
            scope: decoded.scope,
            change: decoded.change,
        })
    }
}
