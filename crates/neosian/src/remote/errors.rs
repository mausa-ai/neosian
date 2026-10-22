//! The wire envelope back to the errors the shared readers already use.
//! Unknown codes or malformed details degrade to NeosianError, like
//! Python's decode_error; their message and details survive verbatim.

use crate::conversation::Refused;
use crate::store::error::{Error, MemoryError};
use serde_json::{Value, json};

pub fn decode(envelope: &Value) -> Error {
    let message = envelope
        .get("message")
        .map(crate::pyfmt::py_str)
        .unwrap_or_default();
    let details = envelope
        .get("details")
        .filter(|v| v.is_object())
        .cloned()
        .unwrap_or(json!({}));
    let code = envelope["code"].as_str().unwrap_or("");
    if code == "value_error" {
        return Error::Value(message);
    }
    if code == "agent_configuration_error" {
        return Error::Memory(MemoryError {
            code: "agent_configuration_error",
            message,
            details,
        });
    }
    Error::Memory(typed(code, &details).unwrap_or(MemoryError {
        code: "neosian_error",
        message,
        details,
    }))
}

fn typed(code: &str, data: &Value) -> Option<MemoryError> {
    let text = |key: &str| data.get(key)?.as_str();
    Some(match code {
        "memory_document_not_found" => MemoryError::not_found(text("scope")?, text("path")?),
        "memory_scope_invalid" => MemoryError::scope_invalid(text("scope")?, text("reason")?),
        "memory_path_invalid" => MemoryError::path_invalid(text("path")?, text("reason")?),
        "memory_format_unsupported" => {
            MemoryError::format_unsupported(text("scope")?, text("path")?, text("reason")?)
        }
        "memory_conflict" => {
            let path = match data.get("path")? {
                Value::Null => None,
                Value::String(path) => Some(path.as_str()),
                _ => return None,
            };
            MemoryError::conflict_versions(
                text("scope")?,
                path,
                text("reason")?,
                data.get("expected_version").and_then(Value::as_u64),
                data.get("actual_version").and_then(Value::as_u64),
            )
        }
        "agent_conversation_id_invalid"
        | "agent_conversation_format_unsupported"
        | "agent_conversation_conflict" => {
            let id = text("conversation_id")?;
            let reason = text("reason")?;
            let refused = match code {
                "agent_conversation_id_invalid" => Refused::id_invalid(id, reason),
                "agent_conversation_format_unsupported" => Refused::format_unsupported(id, reason),
                _ => Refused::conflict(id, reason),
            };
            MemoryError {
                code: refused.code,
                message: refused.message,
                details: refused.details,
            }
        }
        _ => return None,
    })
}
