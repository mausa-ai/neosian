//! A message as the turn log carries it (`llm/codec.py`, `llm/blocks.py`):
//! the read side of the codec, bounded to what the codec writes, the
//! write side for the text messages the record builds, and `text_of`.

use std::borrow::Cow;

use serde_json::{Value, json};

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

/// A content block: only text contributes to what a renderer shows.
#[derive(Debug)]
pub enum Block {
    Text(String),
    Media,
    Compaction,
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
}

#[derive(Debug)]
pub struct Message {
    pub role: Role,
    pub content: Option<Content>,
    pub tool_calls: Vec<ToolCall>,
    pub tool_call_id: Option<String>,
}

/// `message_from_json`: None where the Python codec raises (a missing or
/// mistyped field, an unknown block type or role).
pub fn from_json(data: &Value) -> Option<Message> {
    let data = data.as_object()?;
    let role = Role::parse(data.get("role")?.as_str()?)?;
    let content = match data.get("content") {
        None | Some(Value::Null) => None,
        Some(Value::String(text)) => Some(Content::Text(text.clone())),
        Some(Value::Array(items)) => Some(Content::Blocks(
            items.iter().map(block).collect::<Option<_>>()?,
        )),
        Some(_) => return None,
    };
    let tool_calls = match data.get("tool_calls") {
        Some(Value::Array(calls)) => calls.iter().map(tool_call).collect::<Option<_>>()?,
        Some(value) if !falsy(value) => return None,
        _ => Vec::new(),
    };
    let tool_call_id = match data.get("tool_call_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(id)) => Some(id.clone()).filter(|id| !id.is_empty()),
        Some(_) => return None,
    };
    Some(Message {
        role,
        content,
        tool_calls,
        tool_call_id,
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

fn block(item: &Value) -> Option<Block> {
    let item = item.as_object()?;
    match item.get("type")?.as_str()? {
        "text" => Some(Block::Text(item.get("text")?.as_str()?.to_string())),
        "image" | "document" => {
            // Exactly one of `data` or `url`, and a media type with data.
            let set = |key: &str| item.get(key).is_some_and(|value| !value.is_null());
            (set("data") != set("url") && (!set("data") || set("media_type")))
                .then_some(Block::Media)
        }
        "compaction" => Some(Block::Compaction),
        _ => None,
    }
}

fn tool_call(item: &Value) -> Option<ToolCall> {
    let item = item.as_object()?;
    let id = item.get("id")?.as_str()?;
    let name = item.get("name")?.as_str()?;
    let arguments = match item.get("arguments") {
        None | Some(Value::Null) => Value::Object(serde_json::Map::new()),
        Some(object @ Value::Object(_)) => object.clone(),
        Some(_) => return None,
    };
    Some(ToolCall {
        id: id.to_string(),
        name: name.to_string(),
        arguments,
    })
}

/// `message_to_json`: the five keys in the codec's order, `reasoning`
/// never set by the record; a block list keeps its text blocks.
pub fn to_json(message: &Message) -> Value {
    let content = match &message.content {
        None => Value::Null,
        Some(Content::Text(text)) => json!(text),
        Some(Content::Blocks(blocks)) => Value::Array(
            blocks
                .iter()
                .filter_map(|block| match block {
                    Block::Text(text) => Some(json!({"type": "text", "text": text})),
                    _ => None,
                })
                .collect(),
        ),
    };
    let calls: Vec<Value> = message
        .tool_calls
        .iter()
        .map(|call| json!({"id": call.id, "name": call.name, "arguments": call.arguments}))
        .collect();
    json!({
        "role": message.role.as_str(),
        "content": content,
        "reasoning": Value::Null,
        "tool_calls": calls,
        "tool_call_id": message.tool_call_id,
    })
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
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn a_message_reads_as_the_codec_wrote_it() {
        let message = from_json(&json!({
            "role": "assistant", "content": null, "reasoning": null,
            "tool_calls": [{"id": "t1", "name": "Read", "arguments": {"b": 1, "a": 2}}],
            "tool_call_id": null
        }))
        .unwrap();
        assert_eq!(message.role, Role::Assistant);
        assert_eq!(text_of(&message), "");
        assert_eq!(
            message.tool_calls[0].arguments.to_string(),
            r#"{"b":1,"a":2}"#
        );
        let blocks = from_json(&json!({
            "role": "user",
            "content": [
                {"type": "text", "text": "one"},
                {"type": "image", "url": "https://x/y.png"},
                {"type": "text", "text": "two"}
            ]
        }))
        .unwrap();
        assert_eq!(text_of(&blocks), "one\ntwo");
        let tool = from_json(&json!({"role": "tool", "content": "ok", "tool_call_id": "t1"}));
        assert_eq!(tool.unwrap().tool_call_id.as_deref(), Some("t1"));
        let bare =
            from_json(&json!({"role": "user", "tool_calls": "", "tool_call_id": ""})).unwrap();
        assert!(bare.tool_calls.is_empty() && bare.tool_call_id.is_none());
    }

    #[test]
    fn what_the_codec_refuses_is_none() {
        for data in [
            json!([]),
            json!({"content": "x"}),
            json!({"role": "robot"}),
            json!({"role": "user", "content": 7}),
            json!({"role": "user", "content": [{"type": "video"}]}),
            json!({"role": "user", "content": [{"type": "text"}]}),
            json!({"role": "user", "content": [{"type": "image", "data": "abc"}]}),
            json!({"role": "user", "content": [{"type": "image", "data": "abc", "url": "u"}]}),
            json!({"role": "assistant", "tool_calls": [{"id": "t1"}]}),
            json!({"role": "assistant", "tool_calls": [{"id": 1, "name": "x"}]}),
            json!({"role": "assistant", "tool_calls": [{"id": "t", "name": "x", "arguments": "{}"}]}),
            json!({"role": "assistant", "tool_calls": "x"}),
        ] {
            assert!(from_json(&data).is_none(), "{data}");
        }
    }
}
