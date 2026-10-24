//! Canonical wire encoders shared by the state process and remote restores.

use crate::conversation::{Projection, Redaction, Turn, message};
use crate::repr::repr;
use crate::store::envelope::Envelope;
use crate::store::error::MemoryError;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, scalar};
use serde_json::{Map, Value, json};

/// `encode_timestamp`: `Z`, whatever offset the row kept.
fn stamp(stamp: Stamp) -> String {
    stamp.utc().z()
}

/// The envelope's unknown keys as `MemoryDocument.extra`, bounded to
/// one-line scalars or JSON collections (§39.11, §39.17).
pub(crate) fn extra_json(
    envelope: &Envelope,
    scope: &str,
    path: &str,
) -> Result<Value, MemoryError> {
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
        let (name, raw) = scalar::key_value(line).ok_or_else(|| bounded(&last))?;
        let name = match scalar::read(name) {
            Some(Value::String(unquoted)) => unquoted,
            _ => name.to_string(),
        };
        let Some(value) = scalar::read(raw).or_else(|| serde_json::from_str(raw).ok()) else {
            return Err(bounded(&name));
        };
        last = name.clone();
        extra.insert(name, value);
    }
    Ok(Value::Object(extra))
}

pub fn document(scope: &str, path: &str, document: &Document) -> Result<Value, MemoryError> {
    document_parts(scope, path, &document.content, &document.envelope)
}

pub fn document_parts(
    scope: &str,
    path: &str,
    content: &str,
    envelope: &Envelope,
) -> Result<Value, MemoryError> {
    Ok(json!({
        "scope": scope,
        "path": path,
        "content": content,
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
