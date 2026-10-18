//! A message as the turn log and the wire carry it (`llm/codec.py`,
//! `llm/blocks.py`): the codec whole, both ways. `reasoning` and `extra`
//! ride verbatim as the Python dataclass keeps them; a block of every kind
//! keeps its fields, so a turn a client appends reads back as it was sent
//! (CS5). Where the Python codec raises, `from_json` names the sentence.

use std::borrow::Cow;

use serde_json::{Map, Value, json};

use crate::pyfmt::py_repr;
use crate::text::is_space;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}

impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::System => "system",
            Self::User => "user",
            Self::Assistant => "assistant",
            Self::Tool => "tool",
        }
    }

    fn parse(text: &str) -> Option<Self> {
        match text {
            "system" => Some(Self::System),
            "user" => Some(Self::User),
            "assistant" => Some(Self::Assistant),
            "tool" => Some(Self::Tool),
            _ => None,
        }
    }
}

/// A content block: only text contributes to what a renderer shows; the
/// rest rides through verbatim.
#[derive(Debug)]
pub enum Block {
    Text(String),
    /// An `image` or `document` block: exactly one of `data` or `url`.
    Media {
        kind: &'static str,
        media_type: Option<String>,
        data: Option<String>,
        url: Option<String>,
    },
    Compaction {
        content: Value,
        encrypted_content: Value,
    },
}

#[derive(Debug)]
pub enum Content {
    Text(String),
    Blocks(Vec<Block>),
}

#[derive(Debug)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    /// The arguments object, its keys in the order the row holds them.
    pub arguments: Value,
    /// A provider's own fields, emitted only when truthy.
    pub extra: Value,
}

#[derive(Debug)]
pub struct Message {
    pub role: Role,
    pub content: Option<Content>,
    pub reasoning: Value,
    pub tool_calls: Vec<ToolCall>,
    pub tool_call_id: Option<String>,
    pub extra: Value,
}

impl Message {
    /// A plain text message, as the record builds them.
    pub fn text(role: Role, text: String) -> Self {
        Self {
            role,
            content: Some(Content::Text(text)),
            reasoning: Value::Null,
            tool_calls: Vec::new(),
            tool_call_id: None,
            extra: Value::Null,
        }
    }
}

/// Where `message_from_json` raises: the key a `KeyError` names, a
/// `ValueError`'s sentence, or a shape Python would crash on (a
/// `TypeError`), worded here since that door has no sentence.
#[derive(Debug, PartialEq, Eq)]
pub enum Undecodable {
    Key(&'static str),
    Value(String),
    Type(String),
}

type Decoded<T> = Result<T, Undecodable>;

/// `message_from_json`, the codec's sentences where it refuses.
pub fn from_json(data: &Value) -> Decoded<Message> {
    let Some(data) = data.as_object() else {
        return Err(Undecodable::Type("a message must be an object".to_string()));
    };
    let role = data.get("role").ok_or(Undecodable::Key("role"))?;
    let role = role
        .as_str()
        .and_then(Role::parse)
        .ok_or_else(|| Undecodable::Value(format!("{} is not a valid Role", py_repr(role))))?;
    let content = match data.get("content") {
        None | Some(Value::Null) => None,
        Some(Value::String(text)) => Some(Content::Text(text.clone())),
        Some(Value::Array(items)) => Some(Content::Blocks(
            items.iter().map(block).collect::<Decoded<_>>()?,
        )),
        Some(_) => {
            let shape = "message content must be a string, null or a list of blocks";
            return Err(Undecodable::Type(shape.to_string()));
        }
    };
    let tool_calls = match data.get("tool_calls") {
        Some(Value::Array(calls)) => calls.iter().map(tool_call).collect::<Decoded<_>>()?,
        Some(value) if !falsy(value) => {
            return Err(Undecodable::Type("tool_calls must be a list".to_string()));
        }
        _ => Vec::new(),
    };
    let tool_call_id = match data.get("tool_call_id") {
        Some(Value::String(id)) => Some(id.clone()).filter(|id| !id.is_empty()),
        Some(value) if !falsy(value) => {
            return Err(Undecodable::Type(
                "tool_call_id must be a string".to_string(),
            ));
        }
        _ => None,
    };
    Ok(Message {
        role,
        content,
        reasoning: data.get("reasoning").cloned().unwrap_or(Value::Null),
        tool_calls,
        tool_call_id,
        extra: data.get("extra").cloned().unwrap_or(Value::Null),
    })
}

/// Python's falsiness of a JSON value: what `or []` replaces.
pub fn falsy(value: &Value) -> bool {
    match value {
        Value::Null | Value::Bool(false) => true,
        Value::Number(number) => number.as_f64() == Some(0.0),
        Value::String(text) => text.is_empty(),
        Value::Array(items) => items.is_empty(),
        Value::Object(map) => map.is_empty(),
        Value::Bool(true) => false,
    }
}

/// A string or null field of a block; anything else is a shape Python
/// would crash on.
fn optional_text(item: &Map<String, Value>, key: &str, what: &str) -> Decoded<Option<String>> {
    match item.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(text)) => Ok(Some(text.clone())),
        Some(_) => Err(Undecodable::Type(format!("{what} {key} must be a string"))),
    }
}

fn block(item: &Value) -> Decoded<Block> {
    let Some(item) = item.as_object() else {
        return Err(Undecodable::Type(
            "a content block must be an object".to_string(),
        ));
    };
    let kind = item.get("type").unwrap_or(&Value::Null);
    match kind.as_str() {
        Some("text") => match item.get("text").ok_or(Undecodable::Key("text"))? {
            Value::String(text) => Ok(Block::Text(text.clone())),
            _ => Err(Undecodable::Type(
                "a text block's text must be a string".to_string(),
            )),
        },
        Some(name @ ("image" | "document")) => {
            let block = if name == "image" {
                "ImageBlock"
            } else {
                "DocumentBlock"
            };
            let media_type = optional_text(item, "media_type", block)?;
            let data = optional_text(item, "data", block)?;
            let url = optional_text(item, "url", block)?;
            if data.is_none() == url.is_none() {
                let why = format!("{block} requires exactly one of 'data' or 'url'");
                return Err(Undecodable::Value(why));
            }
            if data.is_some() && media_type.is_none() {
                let why = format!("{block} requires media_type with base64 data");
                return Err(Undecodable::Value(why));
            }
            Ok(Block::Media {
                kind: if name == "image" { "image" } else { "document" },
                media_type,
                // `"".join(data.split())`: the base64 payload without its whitespace.
                data: data.map(|data| data.chars().filter(|ch| !is_space(*ch)).collect()),
                url,
            })
        }
        Some("compaction") => Ok(Block::Compaction {
            content: item.get("content").cloned().unwrap_or(Value::Null),
            encrypted_content: item
                .get("encrypted_content")
                .cloned()
                .unwrap_or(Value::Null),
        }),
        _ => Err(Undecodable::Value(format!(
            "Unknown content block type: {}",
            py_repr(kind)
        ))),
    }
}

fn tool_call(item: &Value) -> Decoded<ToolCall> {
    let Some(item) = item.as_object() else {
        return Err(Undecodable::Type(
            "a tool call must be an object".to_string(),
        ));
    };
    let field = |key: &'static str| -> Decoded<String> {
        match item.get(key).ok_or(Undecodable::Key(key))? {
            Value::String(text) => Ok(text.clone()),
            _ => Err(Undecodable::Value(format!(
                "tool call parameter {} must be a string",
                crate::repr::repr(key)
            ))),
        }
    };
    let (id, name) = (field("id")?, field("name")?);
    let arguments = match item.get("arguments") {
        None | Some(Value::Null) => Value::Object(Map::new()),
        Some(object @ Value::Object(_)) => object.clone(),
        Some(_) => {
            let why = "tool call parameter 'arguments' must be an object";
            return Err(Undecodable::Value(why.to_string()));
        }
    };
    Ok(ToolCall {
        id,
        name,
        arguments,
        extra: item.get("extra").cloned().unwrap_or(Value::Null),
    })
}

fn block_json(block: &Block) -> Value {
    match block {
        Block::Text(text) => json!({"type": "text", "text": text}),
        Block::Media {
            kind,
            media_type,
            data,
            url,
        } => json!({"type": kind, "media_type": media_type, "data": data, "url": url}),
        Block::Compaction {
            content,
            encrypted_content,
        } => json!({
            "type": "compaction",
            "content": content,
            "encrypted_content": encrypted_content,
        }),
    }
}

/// `message_to_json`: the five keys in the codec's order, `extra` on the
/// message and on a call only when truthy.
pub fn to_json(message: &Message) -> Value {
    let content = match &message.content {
        None => Value::Null,
        Some(Content::Text(text)) => json!(text),
        Some(Content::Blocks(blocks)) => Value::Array(blocks.iter().map(block_json).collect()),
    };
    let calls: Vec<Value> = message
        .tool_calls
        .iter()
        .map(|call| {
            let mut call_json =
                json!({"id": call.id, "name": call.name, "arguments": call.arguments});
            if !falsy(&call.extra) {
                call_json["extra"] = call.extra.clone();
            }
            call_json
        })
        .collect();
    let mut encoded = json!({
        "role": message.role.as_str(),
        "content": content,
        "reasoning": message.reasoning,
        "tool_calls": calls,
        "tool_call_id": message.tool_call_id,
    });
    if !falsy(&message.extra) {
        encoded["extra"] = message.extra.clone();
    }
    encoded
}

/// `text_of`: the text, the text blocks joined by newlines, or nothing.
pub fn text_of(message: &Message) -> Cow<'_, str> {
    match &message.content {
        None => Cow::Borrowed(""),
        Some(Content::Text(text)) => Cow::Borrowed(text),
        Some(Content::Blocks(blocks)) => {
            let texts: Vec<&str> = blocks
                .iter()
                .filter_map(|block| match block {
                    Block::Text(text) => Some(text.as_str()),
                    _ => None,
                })
                .collect();
            Cow::Owned(texts.join("\n"))
        }
    }
}

#[cfg(test)]
mod tests;
