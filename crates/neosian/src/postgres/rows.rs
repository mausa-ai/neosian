//! Database row guards precede the shared value codecs, as in postgres/rows.py.

use crate::conversation::{self, Projection, Refused, Turn, message};
use crate::remote::codec;
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::{parse_scope, validate_document_path};
use serde_json::Value;

pub fn address(scope: &str, path: &str) -> Result<(), Error> {
    parse_scope(scope)?;
    validate_document_path(path)?;
    Ok(())
}

pub fn memory_format(scope: &str, path: &str, row: &Value) -> Result<(), Error> {
    let declared = row["neosian_format"].as_i64().unwrap_or(0);
    if !(1..=crate::store::FORMAT as i64).contains(&declared) {
        return Err(MemoryError::format_unsupported(
            scope,
            path,
            &format!("neosian_format {declared} is not supported"),
        )
        .into());
    }
    Ok(())
}

pub fn document(scope: &str, path: &str, row: &Value) -> Result<crate::store::Document, Error> {
    memory_format(scope, path, row)?;
    if !row["extra"].is_object() {
        return Err(MemoryError::format_unsupported(scope, path, "extra is not an object").into());
    }
    codec::document(row)
}

fn turn_format(id: &str, where_: &str, row: &Value) -> Result<(), conversation::Error> {
    let declared = row["neosian_format"].as_i64().unwrap_or(0);
    if !(1..=conversation::FORMAT as i64).contains(&declared) {
        return Err(Refused::format_unsupported(
            id,
            &format!("{where_} declares unsupported format {declared}"),
        )
        .into());
    }
    Ok(())
}

pub fn turn(id: &str, mut row: Value) -> Result<Turn, conversation::Error> {
    let where_ = format!("turn row {}", row["turn"]);
    turn_format(id, &where_, &row)?;
    let refuse = |reason| Refused::format_unsupported(id, &format!("{where_} {reason}"));
    let messages = row["messages"]
        .as_array()
        .ok_or_else(|| refuse("has no message array"))?;
    let redacted = row["redacted"] == true;
    if messages.is_empty() && !redacted {
        return Err(refuse("has no message array").into());
    }
    if !messages.is_empty() && redacted {
        return Err(refuse("is redacted yet carries messages").into());
    }
    for item in messages {
        message::from_json(item).map_err(|_| refuse("has an undecodable message"))?;
    }
    row["conversation_id"] = id.into();
    Ok(codec::turn(&row)?)
}

pub fn projection(id: &str, row: &Value) -> Result<Projection, conversation::Error> {
    let where_ = format!("projection row (turn {})", row["turn"]);
    turn_format(id, &where_, row)?;
    let entry = codec::projection(row)?;
    if entry.turn < 1
        || entry.span < 1
        || entry.span > entry.turn
        || !conversation::rows::KINDS.contains(&entry.kind.as_str())
    {
        return Err(
            Refused::format_unsupported(id, &format!("{where_} has invalid fields")).into(),
        );
    }
    Ok(entry)
}

pub fn window(after: i64, limit: Option<i64>) -> Result<(), conversation::Error> {
    if after < 0 {
        return Err(conversation::Error::Value(format!(
            "after must be >= 0, got {after}"
        )));
    }
    if let Some(limit) = limit.filter(|limit| *limit < 0) {
        return Err(conversation::Error::Value(format!(
            "limit must be >= 0, got {limit}"
        )));
    }
    Ok(())
}

pub fn escaped(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_")
}
