//! The value types as flat JSON (`server/wire.py`, `wire_archive.py`,
//! DESIGN §18.2): the five frozen types out, the archive's rows in,
//! timestamps as ISO-8601 `Z`, `extra` verbatim. The archive is typed at
//! the door like every other parameter; where the Python decoder reads a
//! field untyped, this door requires the type the row holds, so a restore
//! never lands a row the stores cannot read back.

use serde_json::{Map, Value, json};

use super::envelope::Fail;
use super::params::{
    Payload, decode_timestamp, key, optional_int, optional_ints, optional_str, require_int,
    require_str,
};
use crate::conversation::message::{self, Message, falsy};
use crate::conversation::rows::KINDS;
use crate::conversation::{Projection, Redaction, Turn};
use crate::repr::repr;
use crate::store::envelope::Envelope;
use crate::store::error::MemoryError;
use crate::store::journal::{Act, Action, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, Restored, scalar};

/// `encode_timestamp`: `Z`, whatever offset the row kept.
fn stamp(stamp: Stamp) -> String {
    stamp.utc().z()
}

/// The envelope's unknown keys as `MemoryDocument.extra`, bounded to
/// one-line scalars (§39.11).
fn extra_json(envelope: &Envelope, scope: &str, path: &str) -> Result<Value, MemoryError> {
    let mut extra = Map::new();
    let mut last = String::new();
    for line in &envelope.extra {
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let bounded = |key: &str| {
            MemoryError::format_unsupported(
                scope,
                path,
                &format!("extra key {} is not a one-line scalar", repr(key)),
            )
        };
        if line.starts_with([' ', '\t']) || line == "-" || line.starts_with("- ") {
            return Err(bounded(&last));
        }
        let (name, raw) = match line.split_once(": ") {
            Some((name, raw)) => (name, raw.trim()),
            None => (line.strip_suffix(':').unwrap_or(line), ""),
        };
        let name = match scalar::read(name) {
            Some(Value::String(unquoted)) => unquoted,
            _ => name.to_string(),
        };
        let Some(value) = scalar::read(raw) else {
            return Err(bounded(&name));
        };
        last = name.clone();
        extra.insert(name, value);
    }
    Ok(Value::Object(extra))
}

pub fn document(scope: &str, path: &str, document: &Document) -> Result<Value, Fail> {
    let envelope = &document.envelope;
    Ok(json!({
        "scope": scope,
        "path": path,
        "content": document.content,
        "version": envelope.version,
        "created_at": stamp(envelope.created_at),
        "updated_at": stamp(envelope.updated_at),
        "actor": envelope.actor_value(),
        "redacted": envelope.redacted,
        "extra": extra_json(envelope, scope, path)?,
    }))
}

pub fn entry(entry: &Entry) -> Value {
    json!({
        "path": entry.path,
        "version": entry.version,
        "created_at": stamp(entry.created_at),
        "updated_at": stamp(entry.updated_at),
        "redacted": entry.redacted,
    })
}

pub fn version(row: &Row) -> Value {
    json!({
        "path": row.path,
        "version": row.version,
        "action": row.action.as_str(),
        "content": row.content,
        "actor": row.actor,
        "created_at": stamp(row.created_at),
        "redacted": row.redacted,
    })
}

pub fn redaction(act: &Act) -> Value {
    json!({
        "path": act.path,
        "actor": act.actor,
        "created_at": stamp(act.created_at),
        "count": act.count,
    })
}

pub fn turn(turn: &Turn) -> Value {
    let messages: Vec<Value> = turn.messages.iter().map(message::to_json).collect();
    json!({
        "conversation_id": turn.conversation_id,
        "turn": turn.turn,
        "messages": messages,
        "created_at": stamp(turn.created_at),
        "actor": turn.actor,
        "redacted": turn.redacted,
    })
}

pub fn turn_redaction(act: &Redaction) -> Value {
    json!({
        "conversation_id": act.conversation_id,
        "turns": act.turns,
        "actor": act.actor,
        "created_at": stamp(act.created_at),
    })
}

pub fn projection(entry: &Projection) -> Value {
    json!({
        "turn": entry.turn,
        "kind": entry.kind,
        "text": entry.text,
        "span": entry.span,
    })
}

/// `message_from_json` at the door.
pub fn decode_message(data: &Value) -> Result<Message, Fail> {
    Ok(message::from_json(data)?)
}

/// A string field of a nested object: the `KeyError` when absent, the
/// typed sentence otherwise.
fn text(data: &Payload, name: &'static str) -> Result<String, Fail> {
    match key(data, name)? {
        Value::String(text) => Ok(text.clone()),
        _ => Err(Fail::value(format!(
            "parameter {} must be a string",
            repr(name)
        ))),
    }
}

/// A string-or-null field of a nested object.
fn optional_text(data: &Payload, name: &'static str) -> Result<Option<String>, Fail> {
    match key(data, name)? {
        Value::Null => Ok(None),
        Value::String(text) => Ok(Some(text.clone())),
        _ => Err(Fail::value(format!(
            "parameter {} must be a string",
            repr(name)
        ))),
    }
}

fn integer(data: &Payload, name: &'static str) -> Result<u64, Fail> {
    key(data, name)?
        .as_u64()
        .ok_or_else(|| Fail::value(format!("parameter {} must be an integer", repr(name))))
}

fn moment(data: &Payload, name: &'static str) -> Result<Stamp, Fail> {
    decode_timestamp(key(data, name)?)
}

/// `decode_projection`, then the constructor's own checks.
pub fn decode_projection(data: &Payload) -> Result<Projection, Fail> {
    let span = optional_int(data, "span")?;
    let turn = require_int(data, "turn")?;
    let kind = require_str(data, "kind")?;
    let text = require_str(data, "text")?;
    let span = span.unwrap_or(1);
    if turn < 1 {
        return Err(Fail::value(format!("turn must be >= 1, got {turn}")));
    }
    if !(1 <= span && span <= turn) {
        return Err(Fail::value(format!(
            "span must satisfy 1 <= span <= turn, got span={span} for turn={turn}"
        )));
    }
    if !KINDS.contains(&kind.as_str()) {
        return Err(Fail::value(format!(
            "kind must be one of ['digest', 'epoch', 'log'], got {}",
            repr(&kind)
        )));
    }
    Ok(Projection {
        turn: turn as u64,
        kind,
        text,
        span: span as u64,
    })
}

/// `decode_document`: a live document of a scope archive, its `extra`
/// spelled as PyYAML would spell it.
pub fn decode_document(data: &Payload) -> Result<Restored, Fail> {
    text(data, "scope")?;
    let path = text(data, "path")?;
    let content = text(data, "content")?;
    let version = integer(data, "version")?;
    let created_at = moment(data, "created_at")?;
    let updated_at = moment(data, "updated_at")?;
    let actor = optional_text(data, "actor")?;
    let redacted = !falsy(key(data, "redacted")?);
    let mut extra = Vec::new();
    match data.get("extra") {
        None => {}
        Some(value) if falsy(value) => {}
        Some(Value::Object(fields)) => {
            for (name, value) in fields {
                let Some(spelled) = scalar::spell(value) else {
                    let why = format!(
                        "parameter 'extra' holds {}, which is not a scalar",
                        repr(name)
                    );
                    return Err(Fail::value(why));
                };
                extra.push(format!("{}: {spelled}", scalar::spell_str(name)));
            }
        }
        Some(_) => return Err(Fail::value("parameter 'extra' must be an object")),
    }
    Ok(Restored {
        path,
        content,
        envelope: Envelope {
            version,
            created_at,
            updated_at,
            actor: actor.map(|actor| scalar::spell_str(&actor)),
            redacted,
            extra,
        },
    })
}

pub fn decode_version(data: &Payload) -> Result<Row, Fail> {
    let path = text(data, "path")?;
    let version = integer(data, "version")?;
    let action = text(data, "action")?;
    let content = text(data, "content")?;
    let actor = optional_text(data, "actor")?;
    let created_at = moment(data, "created_at")?;
    let redacted = !falsy(key(data, "redacted")?);
    let action = [Action::Created, Action::Modified, Action::Deleted]
        .into_iter()
        .find(|known| known.as_str() == action)
        .ok_or_else(|| {
            Fail::value(format!(
                "parameter 'action' must be one of created, modified, deleted, got {}",
                repr(&action)
            ))
        })?;
    Ok(Row {
        path,
        version,
        action,
        content,
        actor,
        created_at,
        redacted,
    })
}

pub fn decode_redaction(data: &Payload) -> Result<Act, Fail> {
    Ok(Act {
        path: optional_text(data, "path")?,
        actor: optional_text(data, "actor")?,
        created_at: moment(data, "created_at")?,
        count: integer(data, "count")?,
    })
}

pub fn decode_turn(data: &Payload) -> Result<Turn, Fail> {
    let conversation_id = text(data, "conversation_id")?;
    let turn = integer(data, "turn")?;
    let messages = match key(data, "messages")? {
        Value::Array(items) => items
            .iter()
            .map(decode_message)
            .collect::<Result<Vec<Message>, Fail>>()?,
        _ => {
            return Err(Fail::value(
                "parameter 'messages' must be a list of objects",
            ));
        }
    };
    let created_at = moment(data, "created_at")?;
    let actor = match data.get("actor") {
        None | Some(Value::Null) => None,
        Some(Value::String(actor)) => Some(actor.clone()),
        Some(_) => return Err(Fail::value("parameter 'actor' must be a string")),
    };
    Ok(Turn {
        conversation_id,
        turn,
        messages,
        created_at,
        actor,
        redacted: data.get("redacted").is_some_and(|flag| !falsy(flag)),
    })
}

pub fn decode_turn_redaction(data: &Payload) -> Result<Redaction, Fail> {
    let turns = optional_ints(data, "turns")?;
    let Some(turns) = turns.filter(|turns| !turns.is_empty() && turns.iter().all(|t| *t >= 1))
    else {
        return Err(Fail::value(
            "parameter 'turns' must name turn numbers from 1",
        ));
    };
    Ok(Redaction {
        conversation_id: require_str(data, "conversation_id")?,
        turns: turns.into_iter().map(|turn| turn as u64).collect(),
        actor: optional_str(data, "actor")?,
        created_at: moment(data, "created_at")?,
    })
}
