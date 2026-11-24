//! The row a turn stored as records keeps in the turn log (DESIGN §40.54
//! item 4): `neosian_format` 2, no messages, its records in
//! `records/<turn>.jsonl`. A store reads format 2 only once records are
//! enabled on it; anywhere else the row is newer than supported, as before.

use serde_json::{Map, Value, json};

use super::rows::{actor, line, parse_object, stamp, turn_from};
use super::turn_records::check_draft_id;
use super::{Refused, Turn};
use crate::store::time::Stamp;

/// The turn format of a header row.
pub const RECORDS_FORMAT: u64 = 2;

/// A format-2 header: the turn without its messages.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecordHeader {
    pub turn: u64,
    pub created_at: Stamp,
    pub actor: Option<String>,
    pub records: u64,
    pub bytes: u64,
    pub draft: String,
}

/// One turn-log row: a whole turn, or the header of one stored as records.
pub enum Row {
    Whole(Turn),
    Records(RecordHeader),
}

impl Row {
    pub fn turn(&self) -> u64 {
        match self {
            Self::Whole(turn) => turn.turn,
            Self::Records(header) => header.turn,
        }
    }

    pub fn created_at(&self) -> Stamp {
        match self {
            Self::Whole(turn) => turn.created_at,
            Self::Records(header) => header.created_at,
        }
    }
}

const KEYS: [&str; 7] = [
    "neosian_format",
    "turn",
    "created_at",
    "actor",
    "records",
    "bytes",
    "draft",
];

/// A header row in the turn log's spelling: compact, ASCII-escaped.
pub fn render_header(header: &RecordHeader) -> String {
    let mut data = Map::new();
    let values = [
        json!(RECORDS_FORMAT),
        json!(header.turn),
        json!(header.created_at.z()),
        json!(header.actor),
        json!(header.records),
        json!(header.bytes),
        json!(header.draft),
    ];
    for (key, value) in KEYS.iter().zip(values) {
        data.insert((*key).to_string(), value);
    }
    line(data)
}

/// One turn-log row; `records` says whether the store reads format 2.
pub fn parse_row(
    text: &str,
    number: usize,
    conversation_id: &str,
    records: bool,
) -> Result<Row, Refused> {
    if !records {
        return super::rows::parse_turn(text, number, conversation_id).map(Row::Whole);
    }
    let place = format!("turn-log line {number}");
    let data = parse_object(text, &place, conversation_id, RECORDS_FORMAT)?;
    if data.get("neosian_format").and_then(Value::as_u64) != Some(RECORDS_FORMAT) {
        return turn_from(data, &place, conversation_id).map(Row::Whole);
    }
    let invalid =
        || Refused::format_unsupported(conversation_id, &format!("{place} has invalid fields"));
    if data.keys().any(|key| !KEYS.contains(&key.as_str())) || data.len() != KEYS.len() {
        return Err(invalid());
    }
    let number = |key: &str, least: u64| {
        data.get(key)
            .and_then(Value::as_u64)
            .filter(|n| *n >= least)
    };
    let draft = data
        .get("draft")
        .and_then(Value::as_str)
        .filter(|id| check_draft_id(id).is_ok());
    let (Some(turn), Some(records), Some(bytes), Some(draft), Some(actor)) = (
        number("turn", 1),
        number("records", 1),
        number("bytes", 0),
        draft,
        actor(data.get("actor")),
    ) else {
        return Err(invalid());
    };
    Ok(Row::Records(RecordHeader {
        turn,
        created_at: stamp(data.get("created_at"), &place, conversation_id)?,
        actor,
        records,
        bytes,
        draft: draft.to_string(),
    }))
}
