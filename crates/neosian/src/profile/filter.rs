//! Capture selection and deterministic scrubbing, applied before reducing or spooling.

use super::model::{Capture, Field};
use crate::record::payload::{Payload, Record, reduce_payload};
use serde_json::{Value, json};

fn scrub(value: &mut Value, policy: &Capture) {
    match value {
        Value::String(text) => {
            for replacement in &policy.replacements {
                *text = text.replace(&replacement.from, &replacement.to);
            }
        }
        Value::Array(values) => {
            for value in values {
                scrub(value, policy);
            }
        }
        Value::Object(values) => {
            values.retain(|key, _| !policy.remove_keys.contains(key));
            for value in values.values_mut() {
                scrub(value, policy);
            }
        }
        _ => {}
    }
}

pub fn record(payload: &Payload, policy: &Capture) -> Option<Record> {
    let event = payload
        .get("hook_event_name")
        .and_then(Value::as_str)
        .unwrap_or("");
    let has = |f| policy.fields.contains(&f);
    if event == "UserPromptSubmit" && !has(Field::Prompt)
        || event == "MailboxReceived" && !has(Field::ReceivedContext)
    {
        return None;
    }
    if event == "PostToolUse" {
        let name = payload
            .get("tool_name")
            .and_then(Value::as_str)
            .unwrap_or("");
        if policy.exclude_tools.iter().any(|n| n == name)
            || !has(Field::ToolInputs) && !has(Field::ToolOutputs)
        {
            return None;
        }
    }
    let mut cleaned = payload.clone();
    for key in [
        "prompt",
        "user_prompt",
        "last_assistant_message",
        "text",
        "tool_input",
        "tool_response",
        "tool_name",
        "tool_use_id",
    ] {
        if let Some(value) = cleaned.get_mut(key) {
            scrub(value, policy);
        }
    }
    if !has(Field::Assistant) {
        cleaned.insert("last_assistant_message".into(), json!(""));
        // Cursor still needs its response marker to close a completed span.
        if event == "AssistantResponse" {
            cleaned.insert("text".into(), json!(""));
        }
    }
    if !has(Field::ToolInputs) {
        cleaned.insert("tool_input".into(), json!({}));
    }
    if !has(Field::ToolOutputs) {
        cleaned.insert("tool_response".into(), json!(""));
    }
    let (_, record) = reduce_payload(&cleaned);
    record
}
