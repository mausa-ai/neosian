//! Client-bound mailbox operations (`record/mailbox.py`, DESIGN §34): the
//! plugins' poll and delivery events, one wake reserved per poll and the
//! outcome of its delivery recorded, and the lineage note a continue
//! call leaves (§33). No model work in the hook process.

use serde_json::{Value, json};

use super::payload::Payload;
use crate::conversation::Error;
use crate::messaging::delivery::wake_text;
use crate::messaging::mailbox::{LIST_LIMIT, ListStatus, Mailbox};
use crate::messaging::types::Action;
use crate::messaging::writes::Update;
use crate::pyfmt::py_str;

const CONTINUE_HEADER: &str = "[continuing conversation ";

/// The wake deliveries pending for this session, the first one reserved
/// and handed to the plugin; a batch surfaces one wake per poll.
pub fn poll(mailbox: &Mailbox) -> Result<Vec<Value>, Error> {
    for receipt in mailbox.list(None, ListStatus::Unread, LIST_LIMIT, true, true)? {
        let item = &receipt.message;
        let reserve = Update {
            occurrence: item.occurrence,
            token: None,
            outcome: None,
            due_at: None,
            delay_seconds: None,
        };
        match mailbox.update(&item.scope, &item.id, Action::Reserve, &reserve) {
            Ok(reserved) => {
                return Ok(vec![json!({
                    "id": item.id,
                    "scope": item.scope,
                    "occurrence": item.occurrence,
                    "token": reserved.message.wake_token,
                    "text": wake_text(item),
                })]);
            }
            Err(Error::Value(_)) => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(Vec::new())
}

fn field(payload: &Payload, key: &str) -> Result<String, Error> {
    payload
        .get(key)
        .map(py_str)
        .ok_or_else(|| Error::Value(format!("the payload carries no {key}")))
}

/// The plugin's report on a reserved wake: accepted by the client, or
/// released for a later poll to retry.
pub fn deliver(mailbox: &Mailbox, payload: &Payload) -> Result<(), Error> {
    let occurrence = match payload.get("occurrence") {
        Some(Value::Number(number)) if number.is_i64() => number.as_i64().unwrap_or_default(),
        Some(Value::String(text)) if text.trim().parse::<i64>().is_ok() => {
            text.trim().parse().unwrap_or_default()
        }
        _ => {
            return Err(Error::Value(
                "the payload carries no occurrence".to_string(),
            ));
        }
    };
    let action = if payload.get("accepted") == Some(&Value::Bool(true)) {
        Action::Accepted
    } else {
        Action::Retry
    };
    let token = field(payload, "token")?;
    let report = Update {
        occurrence,
        token: Some(&token),
        outcome: None,
        due_at: None,
        delay_seconds: None,
    };
    mailbox.update(
        &field(payload, "scope")?,
        &field(payload, "message_id")?,
        action,
        &report,
    )?;
    Ok(())
}

/// A tool round that continued a session declares the lineage now: the
/// call's name and the header its result begins with, read from the
/// payload as the record reads them from the turn.
pub fn note_continue(mailbox: &Mailbox, scope: &str, payload: &Payload) -> Result<(), Error> {
    let name = payload.get("tool_name").map(py_str).unwrap_or_default();
    if !name.ends_with("continue_session") {
        return Ok(());
    }
    let response = payload.get("tool_response").map(py_str).unwrap_or_default();
    let Some(found) = response.find(CONTINUE_HEADER) else {
        return Ok(());
    };
    let rest = &response[found + CONTINUE_HEADER.len()..];
    let parent: String = rest
        .chars()
        .take_while(|ch| ch.is_ascii_alphanumeric() || "_.-".contains(*ch))
        .collect();
    if parent.is_empty() {
        return Ok(());
    }
    mailbox.note_continuation(scope, &parent)
}
