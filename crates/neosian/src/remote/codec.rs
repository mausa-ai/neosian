//! Decode wire values, without imposing the file envelope's scalar-only
//! metadata bound. A remote document's extra data is kept as YAML-compatible
//! JSON lines; it is never written through the local file serializer.

use crate::conversation::message;
use crate::conversation::{Projection, Turn};
use crate::store::envelope::Envelope;
use crate::store::error::Error;
use crate::store::journal::{Action, Row};
use crate::store::scalar::spell_str;
use crate::store::time::Stamp;
use crate::store::{Document, Entry};
use serde_json::Value;

pub fn malformed(name: &str) -> Error {
    Error::Value(format!("malformed wire response: {name}"))
}
pub fn key<'a>(data: &'a Value, name: &str) -> Result<&'a Value, Error> {
    data.get(name).ok_or_else(|| malformed(name))
}
pub fn text(data: &Value, name: &str) -> Result<String, Error> {
    key(data, name)?
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| malformed(name))
}
pub fn number(data: &Value, name: &str) -> Result<u64, Error> {
    key(data, name)?.as_u64().ok_or_else(|| malformed(name))
}
pub fn array<'a>(data: &'a Value, name: &str) -> Result<&'a Vec<Value>, Error> {
    key(data, name)?.as_array().ok_or_else(|| malformed(name))
}
fn actor(data: &Value) -> Result<Option<String>, Error> {
    match data.get("actor") {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(actor)) => Ok(Some(actor.clone())),
        _ => Err(malformed("actor")),
    }
}
fn moment(data: &Value, name: &str) -> Result<Stamp, Error> {
    Stamp::parse(&text(data, name)?).map_err(|_| malformed(name))
}
fn redacted(data: &Value) -> Result<bool, Error> {
    match data.get("redacted") {
        None => Ok(false),
        Some(Value::Bool(flag)) => Ok(*flag),
        _ => Err(malformed("redacted")),
    }
}
pub fn document(data: &Value) -> Result<Document, Error> {
    let extra = match data.get("extra") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Object(fields)) => fields
            .iter()
            .map(|(key, value)| {
                format!(
                    "{}: {}",
                    serde_json::to_string(key).expect("string JSON"),
                    value
                )
            })
            .collect(),
        _ => return Err(malformed("extra")),
    };
    Ok(Document {
        content: text(data, "content")?,
        envelope: Envelope {
            version: number(data, "version")?,
            created_at: moment(data, "created_at")?,
            updated_at: moment(data, "updated_at")?,
            actor: actor(data)?.map(|value| spell_str(&value)),
            redacted: redacted(data)?,
            extra,
        },
    })
}
pub fn entry(data: &Value) -> Result<Entry, Error> {
    Ok(Entry {
        path: text(data, "path")?,
        version: number(data, "version")?,
        created_at: moment(data, "created_at")?,
        updated_at: moment(data, "updated_at")?,
        redacted: redacted(data)?,
    })
}
pub fn version(data: &Value) -> Result<Row, Error> {
    let action = match text(data, "action")?.as_str() {
        "created" => Action::Created,
        "modified" => Action::Modified,
        "deleted" => Action::Deleted,
        _ => return Err(malformed("action")),
    };
    Ok(Row {
        path: text(data, "path")?,
        version: number(data, "version")?,
        action,
        content: text(data, "content")?,
        actor: actor(data)?,
        created_at: moment(data, "created_at")?,
        redacted: redacted(data)?,
    })
}
pub fn turn(data: &Value) -> Result<Turn, Error> {
    let messages = array(data, "messages")?
        .iter()
        .map(|data| message::from_json(data).map_err(|_| malformed("message")))
        .collect::<Result<_, _>>()?;
    Ok(Turn {
        conversation_id: text(data, "conversation_id")?,
        turn: number(data, "turn")?,
        messages,
        created_at: moment(data, "created_at")?,
        actor: actor(data)?,
        redacted: redacted(data)?,
    })
}
pub fn projection(data: &Value) -> Result<Projection, Error> {
    Ok(Projection {
        turn: number(data, "turn")?,
        kind: text(data, "kind")?,
        text: text(data, "text")?,
        span: number(data, "span")?,
    })
}
