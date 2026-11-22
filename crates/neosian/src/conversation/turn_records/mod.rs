//! Turn records (DESIGN §40.54 items 3, 12 and 14): a turn's messages as
//! bounded pieces, each with a kind and a place, and back byte for byte;
//! the split rule; the canonical encodings and their digests; the size
//! rule. Named apart from `record`, the recorder. Nothing here is reachable
//! from a route or a command yet; the FileStore writes drafts from T1b1a,
//! and the wire (T1d) removes the allowance on the module's declaration.

mod check;
mod convert;
mod digest;
mod draft;
mod facts;
mod json;
mod read;
mod rebuild;
// The restore reaches no route before the wire (T1d); the allowance goes with it.
#[cfg_attr(not(test), allow(dead_code))]
mod restore;
mod search;
mod size;
mod split;
mod view;

#[cfg(test)]
mod tests;

pub use check::{DraftCheck, Refusal};
pub use convert::{each_record, records_of};
pub use digest::{Header, UnitDigest};
pub use draft::{State, Write, Written, check_draft_id, check_id, conflict, size_refusal};
pub use facts::TextFacts;
pub use json::{JsonCheck, Shape, depth};
pub use read::{ReadItem, ReadPosition, RecordRead, head, tail};
pub use rebuild::messages_of;
#[cfg_attr(not(test), allow(unused_imports))]
pub use restore::{
    Item, Placed, Position, Restore, RestoreStream, Restored, Staged, StreamRefusal, restore_id,
};
pub use search::{
    RecordHit, RecordKey, RecordRanking, RecordSearch, SEARCHABLE, record_excerpt, record_facts,
};
pub use size::Sizes;
pub use split::{encoded_len, split};
pub use view::Viewer;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use super::message::Role;

/// One record's `text`, as its JSON string encoding, quotes included.
pub const TEXT_BYTES: usize = 65_536;
/// A record's `call_id` or `name`, in UTF-8 bytes.
pub const ID_BYTES: usize = 1_024;
/// One record as written or answered, compact JSON.
pub const RECORD_BYTES: usize = 81_920;
/// How far back from a full prefix the split rule looks for a line break
/// or a space, in UTF-8 bytes.
pub const WINDOW_BYTES: usize = 4_096;

/// What a record holds (§40.54 item 3.2).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Prompt,
    Reply,
    System,
    ToolResult,
    ToolCall,
    Reasoning,
    Attachment,
    Extra,
}

impl Kind {
    pub const ALL: [Self; 8] = [
        Self::Prompt,
        Self::Reply,
        Self::System,
        Self::ToolResult,
        Self::ToolCall,
        Self::Reasoning,
        Self::Attachment,
        Self::Extra,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Prompt => "prompt",
            Self::Reply => "reply",
            Self::System => "system",
            Self::ToolResult => "tool_result",
            Self::ToolCall => "tool_call",
            Self::Reasoning => "reasoning",
            Self::Attachment => "attachment",
            Self::Extra => "extra",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|kind| kind.as_str() == text)
    }

    /// The five kinds a search matches.
    pub fn searchable(self) -> bool {
        !matches!(self, Self::Reasoning | Self::Attachment | Self::Extra)
    }

    /// The kind of a message's text content, by its role.
    pub fn of_role(role: Role) -> Self {
        match role {
            Role::User => Self::Prompt,
            Role::Assistant => Self::Reply,
            Role::System => Self::System,
            Role::Tool => Self::ToolResult,
        }
    }
}

/// A message's content form, carried by its first record.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Form {
    Text,
    Blocks,
    None,
}

impl Form {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Blocks => "blocks",
            Self::None => "none",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        [Self::Text, Self::Blocks, Self::None]
            .into_iter()
            .find(|form| form.as_str() == text)
    }
}

/// One bounded piece of one message of one turn (§40.54 item 3.1); the
/// turn number travels beside it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TurnRecord {
    pub seq: u64,
    pub message: u64,
    pub role: Role,
    pub kind: Kind,
    pub content: Option<Form>,
    pub block: Option<u64>,
    pub call: Option<u64>,
    pub call_id: Option<String>,
    pub name: Option<String>,
    pub chunk: u64,
    pub size: u64,
    pub text: String,
}

/// The keys of a record, in §40.54's order; `turn` only in restore shape.
const KEYS: [&str; 13] = [
    "turn", "seq", "message", "role", "kind", "content", "block", "call", "call_id", "name",
    "chunk", "size", "text",
];

impl TurnRecord {
    /// Write shape, or restore shape with `turn`: every key in order,
    /// null ones included.
    pub fn to_json(&self, turn: Option<u64>) -> Value {
        let mut map = Map::new();
        if let Some(turn) = turn {
            map.insert("turn".into(), json!(turn));
        }
        let fields = [
            json!(self.seq),
            json!(self.message),
            json!(self.role.as_str()),
            json!(self.kind.as_str()),
            json!(self.content.map(Form::as_str)),
            json!(self.block),
            json!(self.call),
            json!(self.call_id),
            json!(self.name),
            json!(self.chunk),
            json!(self.size),
            json!(self.text),
        ];
        for (key, value) in KEYS[1..].iter().zip(fields) {
            map.insert((*key).into(), value);
        }
        Value::Object(map)
    }

    /// A record in write or restore shape: unknown keys and mistyped values
    /// refused, a nullable key either null or absent (§40.54 item 8.2).
    pub fn from_json(value: &Value) -> Result<(Option<u64>, Self), String> {
        let map = value.as_object().ok_or("a record must be an object")?;
        if let Some(key) = map.keys().find(|key| !KEYS.contains(&key.as_str())) {
            return Err(format!("unknown record field {key:?}"));
        }
        let number = |key: &str| -> Result<Option<u64>, String> {
            match map.get(key) {
                None | Some(Value::Null) => Ok(None),
                Some(value) => value.as_u64().map(Some).ok_or(format!(
                    "record field {key:?} must be a non-negative integer"
                )),
            }
        };
        let string = |key: &str| -> Result<Option<String>, String> {
            match map.get(key) {
                None | Some(Value::Null) => Ok(None),
                Some(Value::String(text)) => Ok(Some(text.clone())),
                Some(_) => Err(format!("record field {key:?} must be a string")),
            }
        };
        let need = |key: &str, found: Option<u64>| {
            found.ok_or(format!("record field {key:?} is required"))
        };
        let turn = match (map.get("turn"), number("turn")?) {
            (Some(Value::Null), _) | (_, Some(0)) => {
                return Err("record field \"turn\" must be an integer >= 1".into());
            }
            (_, turn) => turn,
        };
        let role = string("role")?.ok_or("record field \"role\" is required")?;
        let kind = string("kind")?.ok_or("record field \"kind\" is required")?;
        let content = string("content")?;
        let record = Self {
            seq: need("seq", number("seq")?)?,
            message: need("message", number("message")?)?,
            role: Role::parse(&role).ok_or(format!("unknown record role {role:?}"))?,
            kind: Kind::parse(&kind).ok_or(format!("unknown record kind {kind:?}"))?,
            content: match content {
                None => None,
                Some(form) => {
                    Some(Form::parse(&form).ok_or(format!("unknown content form {form:?}"))?)
                }
            },
            block: number("block")?,
            call: number("call")?,
            call_id: string("call_id")?,
            name: string("name")?,
            chunk: need("chunk", number("chunk")?)?,
            size: need("size", number("size")?)?,
            text: string("text")?.ok_or("record field \"text\" is required")?,
        };
        Ok((turn, record))
    }

    /// One record alone (§40.54 items 3.1 and 3.4): its bounds, its kind
    /// against its role, and the fields its kind carries.
    pub fn check(&self) -> Result<(), String> {
        let at = format!("record {}", self.seq);
        if encoded_len(&self.text) > TEXT_BYTES {
            return Err(format!(
                "{at}: text encodes to more than {TEXT_BYTES} bytes"
            ));
        }
        for (key, value) in [("call_id", &self.call_id), ("name", &self.name)] {
            if value.as_ref().is_some_and(|value| value.len() > ID_BYTES) {
                return Err(format!("{at}: {key} is longer than {ID_BYTES} bytes"));
            }
        }
        let content_kind = self.kind == Kind::of_role(self.role) || self.kind == Kind::Attachment;
        let shaped = match self.kind {
            Kind::ToolCall => self.call.is_some() && self.name.is_some() && self.call_id.is_some(),
            Kind::Extra => self.name.is_none(),
            Kind::Reasoning => self.call.is_none() && self.name.is_none(),
            Kind::Attachment => self.block.is_some() && self.call.is_none() && self.name.is_none(),
            _ => content_kind && self.call.is_none() && self.name.is_none(),
        };
        if !shaped || (self.block.is_some() && !content_kind) {
            let (role, kind) = (self.role.as_str(), self.kind.as_str());
            return Err(format!(
                "{at}: a {kind} record of this shape cannot belong to a message of role {role}"
            ));
        }
        let length = self.text.len() as u64;
        if length > self.size || (self.text.is_empty() && self.size > 0) {
            return Err(format!(
                "{at}: chunk {} does not fit size {}",
                self.chunk, self.size
            ));
        }
        if self.chunk > 0 && self.content.is_some() {
            return Err(format!(
                "{at}: only a message's first record carries content"
            ));
        }
        Ok(())
    }
}
