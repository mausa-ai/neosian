//! Mailbox values as the Python side writes them (`messaging/types.py`,
//! DESIGN §34): one message is a document under `messages/` whose body is
//! pydantic's JSON. Read here bounded to that output and to the fields
//! the reads use; `encode` and the transitions land with the `messages`
//! tool (#360).

use serde_json::{Map, Value};

use crate::store::time::Stamp;

pub const PREFIX: &str = "messages/";

/// One canonical message; the store's versions retain every prior outcome.
#[derive(Debug, Clone)]
pub struct InboxMessage {
    pub id: String,
    pub scope: String,
    pub about: Option<String>,
    pub about_turn: Option<u64>,
    pub actor: String,
    pub body: String,
    pub created_at: Stamp,
    pub actionable: bool,
    pub occurrence: u64,
    pub status: String,
    pub outcome: Option<String>,
}

/// `messages/<id>`, the id a canonical UUID as `str(UUID(id))` spells it;
/// `UUID()`'s refusal otherwise.
pub fn message_path(id: &str) -> Result<String, String> {
    let canonical = id.len() == 36
        && id.bytes().enumerate().all(|(at, byte)| match at {
            8 | 13 | 18 | 23 => byte == b'-',
            _ => byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase(),
        });
    if canonical {
        Ok(format!("{PREFIX}{id}"))
    } else {
        Err("badly formed hexadecimal UUID string".to_string())
    }
}

/// `types.decode` over pydantic's own output: the declared fields, a
/// stamp as `isoformat()` spells it; anything else is a corrupt document.
pub fn decode(content: &str) -> Result<InboxMessage, String> {
    let value: Value =
        serde_json::from_str(content).map_err(|_| "message document is not JSON".to_string())?;
    let Value::Object(map) = value else {
        return Err("message document is not a JSON object".to_string());
    };
    let text = |key: &str| -> Result<Option<String>, String> {
        match map.get(key) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::String(text)) => Ok(Some(text.clone())),
            Some(_) => Err(format!("message field {key} is not a string")),
        }
    };
    let required = |key: &str| text(key)?.ok_or_else(|| format!("message field {key} is missing"));
    let stamp = |key: &str| -> Result<Option<Stamp>, String> {
        text(key)?
            .map(|found| {
                Stamp::parse(&found).map_err(|_| format!("message field {key} is no stamp"))
            })
            .transpose()
    };
    if map.get("format").and_then(Value::as_u64) != Some(1) {
        return Err("message format is not 1".to_string());
    }
    Ok(InboxMessage {
        id: required("id")?,
        scope: required("scope")?,
        about: text("about")?,
        about_turn: integer(&map, "about_turn")?,
        actor: required("actor")?,
        body: required("body")?,
        created_at: stamp("created_at")?.ok_or("message field created_at is missing")?,
        actionable: map
            .get("actionable")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        occurrence: integer(&map, "occurrence")?.unwrap_or(1),
        status: text("status")?.unwrap_or_else(|| "open".to_string()),
        outcome: text("outcome")?,
    })
}

fn integer(map: &Map<String, Value>, key: &str) -> Result<Option<u64>, String> {
    match map.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(number)) => number
            .as_u64()
            .map(Some)
            .ok_or_else(|| format!("message field {key} is not a count")),
        Some(_) => Err(format!("message field {key} is not an integer")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const WRITTEN: &str = r#"{
  "format": 1,
  "id": "45d78ebb-d289-4f34-981a-43f857ff8cf8",
  "scope": "user:me/proj:demo",
  "conversation": null,
  "turn": null,
  "source": null,
  "about": "early",
  "about_turn": 1,
  "actor": "cli:local",
  "body": "Check upstream issue #123",
  "created_at": "2026-10-13T21:56:33.425486Z",
  "due_at": "2026-10-13T21:56:33.425486Z",
  "delivery": "next_activity",
  "actionable": false,
  "occurrence": 1,
  "status": "open",
  "read_by": null,
  "read_at": null,
  "owner": null,
  "claim_token": null,
  "claim_until": null,
  "outcome": null,
  "wake_owner": null,
  "wake_token": null,
  "wake_until": null,
  "wake_accepted": false
}
"#;

    #[test]
    fn a_message_reads_back_as_pydantic_wrote_it() {
        let message = decode(WRITTEN).unwrap();
        assert_eq!(message.id, "45d78ebb-d289-4f34-981a-43f857ff8cf8");
        assert_eq!(
            (
                message.about.as_deref(),
                message.about_turn,
                message.occurrence
            ),
            (Some("early"), Some(1), 1)
        );
        assert_eq!(message.created_at.z(), "2026-10-13T21:56:33.425486Z");
        assert_eq!((message.status.as_str(), message.outcome), ("open", None));
        assert!(!message.actionable);
    }

    #[test]
    fn a_corrupt_document_is_refused_with_a_sentence() {
        assert_eq!(
            decode("not json").err().as_deref(),
            Some("message document is not JSON")
        );
        assert!(decode(&WRITTEN.replace("\"format\": 1", "\"format\": 2")).is_err());
        assert!(
            decode(&WRITTEN.replace("\"body\": \"Check upstream issue #123\",\n", "")).is_err()
        );
        assert!(decode(&WRITTEN.replace("\"about_turn\": 1", "\"about_turn\": -1")).is_err());
        assert_eq!(
            message_path("45d78ebb-d289-4f34-981a-43f857ff8cf8").as_deref(),
            Ok("messages/45d78ebb-d289-4f34-981a-43f857ff8cf8")
        );
        assert!(message_path("45D78EBB-d289-4f34-981a-43f857ff8cf8").is_err());
        assert!(message_path("x").is_err());
    }
}
