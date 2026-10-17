//! Mailbox values and their versioned document codec (`messaging/types.py`,
//! DESIGN §34): one message is a document under `messages/` whose body is
//! pydantic's JSON. Read bounded to that output, written as pydantic writes
//! it (the declared fields in order, two-space indent, stamps in UTC with
//! `Z`); the lifecycle's constants are `MailboxConfig()`'s.

use serde_json::{Map, Value, json};

use crate::conversation::ids::parse_conversation_id;
use crate::store::grammar::parse_scope;
use crate::store::time::Stamp;

pub const PREFIX: &str = "messages/";
/// `MailboxConfig()`: the context budget, a claim's lease and a wake
/// reservation, in characters and seconds.
pub const CONTEXT_CHARS: usize = 2048;
pub const CLAIM_SECONDS: i64 = 1800;
pub const RESERVATION_SECONDS: i64 = 60;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Delivery {
    NextActivity,
    Wake,
}

impl Delivery {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::NextActivity => "next_activity",
            Self::Wake => "wake",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        [Self::NextActivity, Self::Wake]
            .into_iter()
            .find(|mode| mode.as_str() == text)
    }
}

/// A lifecycle action; `reserve`, `accepted` and `retry` are the wake
/// receiver's alone.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Ack,
    Claim,
    Renew,
    Release,
    Complete,
    Snooze,
    Cancel,
    Reserve,
    Accepted,
    Retry,
}

impl Action {
    pub const ALL: [Self; 10] = [
        Self::Ack,
        Self::Claim,
        Self::Renew,
        Self::Release,
        Self::Complete,
        Self::Snooze,
        Self::Cancel,
        Self::Reserve,
        Self::Accepted,
        Self::Retry,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ack => "ack",
            Self::Claim => "claim",
            Self::Renew => "renew",
            Self::Release => "release",
            Self::Complete => "complete",
            Self::Snooze => "snooze",
            Self::Cancel => "cancel",
            Self::Reserve => "reserve",
            Self::Accepted => "accepted",
            Self::Retry => "retry",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|action| action.as_str() == text)
    }
}

/// One canonical message; the store's versions retain every prior outcome.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InboxMessage {
    pub id: String,
    pub scope: String,
    pub conversation: Option<String>,
    pub turn: Option<i64>,
    pub source: Option<String>,
    pub about: Option<String>,
    pub about_turn: Option<i64>,
    pub actor: String,
    pub body: String,
    pub created_at: Stamp,
    pub due_at: Stamp,
    pub delivery: Delivery,
    pub actionable: bool,
    pub occurrence: i64,
    pub status: String,
    pub read_by: Option<String>,
    pub read_at: Option<Stamp>,
    pub owner: Option<String>,
    pub claim_token: Option<String>,
    pub claim_until: Option<Stamp>,
    pub outcome: Option<String>,
    pub wake_owner: Option<String>,
    pub wake_token: Option<String>,
    pub wake_until: Option<Stamp>,
    pub wake_accepted: bool,
}

impl InboxMessage {
    pub fn available(&self, now: Stamp) -> bool {
        self.status == "open" && self.due_at.instant() <= now.instant()
    }

    pub fn claimed(&self, now: Stamp) -> bool {
        self.owner.is_some()
            && self
                .claim_until
                .is_some_and(|until| until.instant() > now.instant())
    }

    /// Due and open, and never read or read under a claim that lapsed.
    pub fn unread(&self, now: Stamp) -> bool {
        let expired = self.owner.is_some() && !self.claimed(now);
        self.available(now) && (self.read_at.is_none() || expired)
    }
}

/// `MessageTarget`'s rule: a scope of the grammar, a conversation of its
/// own, and a turn only beside a conversation and from 1; the refusal is
/// the sentence the Python side raises.
pub fn check_target(
    scope: &str,
    conversation: Option<&str>,
    turn: Option<i64>,
) -> Result<(), String> {
    parse_scope(scope).map_err(|error| error.message)?;
    if let Some(conversation) = conversation {
        parse_conversation_id(conversation).map_err(|refused| refused.message)?;
    }
    if turn.is_some_and(|turn| turn < 1 || conversation.is_none()) {
        return Err("a turn reference needs a conversation and turn >= 1".to_string());
    }
    Ok(())
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

/// When a message is due: `due_at` in UTC, `now` plus the delay, or now;
/// both named, or a delay that is not positive, is refused.
pub fn due_time(
    now: Stamp,
    due_at: Option<Stamp>,
    delay_seconds: Option<i64>,
) -> Result<Stamp, String> {
    match (due_at, delay_seconds) {
        (Some(_), Some(_)) => Err("give due_at or delay_seconds, not both".to_string()),
        (_, Some(delay)) if delay <= 0 => Err("delay_seconds must be positive".to_string()),
        (_, Some(delay)) => Ok(now.utc().after(delay)),
        (Some(due), None) => Ok(due.utc()),
        (None, None) => Ok(now.utc()),
    }
}

/// The document body as `model_dump_json(indent=2)` writes it, a newline
/// after; `decode` reads it back.
pub fn encode(message: &InboxMessage) -> String {
    let text = serde_json::to_string_pretty(&Value::Object(to_value(message)))
        .expect("a message serializes");
    format!("{text}\n")
}

/// `model_dump(mode="json")`: the declared fields in order.
pub fn to_value(message: &InboxMessage) -> Map<String, Value> {
    let stamp = |stamp: Option<Stamp>| stamp.map_or(Value::Null, |stamp| json!(stamp.utc().z()));
    let pairs: [(&str, Value); 26] = [
        ("format", json!(1)),
        ("id", json!(message.id)),
        ("scope", json!(message.scope)),
        ("conversation", json!(message.conversation)),
        ("turn", json!(message.turn)),
        ("source", json!(message.source)),
        ("about", json!(message.about)),
        ("about_turn", json!(message.about_turn)),
        ("actor", json!(message.actor)),
        ("body", json!(message.body)),
        ("created_at", stamp(Some(message.created_at))),
        ("due_at", stamp(Some(message.due_at))),
        ("delivery", json!(message.delivery.as_str())),
        ("actionable", json!(message.actionable)),
        ("occurrence", json!(message.occurrence)),
        ("status", json!(message.status)),
        ("read_by", json!(message.read_by)),
        ("read_at", stamp(message.read_at)),
        ("owner", json!(message.owner)),
        ("claim_token", json!(message.claim_token)),
        ("claim_until", stamp(message.claim_until)),
        ("outcome", json!(message.outcome)),
        ("wake_owner", json!(message.wake_owner)),
        ("wake_token", json!(message.wake_token)),
        ("wake_until", stamp(message.wake_until)),
        ("wake_accepted", json!(message.wake_accepted)),
    ];
    pairs
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect()
}

/// `types.decode` over pydantic's own output: the declared fields, a
/// stamp as `isoformat()` spells it, the addresses held to their grammars;
/// anything else is a corrupt document.
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
    let flag = |key: &str| -> Result<bool, String> {
        match map.get(key) {
            None | Some(Value::Null) => Ok(false),
            Some(Value::Bool(flag)) => Ok(*flag),
            Some(_) => Err(format!("message field {key} is not a boolean")),
        }
    };
    if map.get("format").and_then(Value::as_u64) != Some(1) {
        return Err("message format is not 1".to_string());
    }
    let delivery = match text("delivery")? {
        None => Delivery::NextActivity,
        Some(mode) => Delivery::parse(&mode).ok_or("message delivery is unknown")?,
    };
    let message = InboxMessage {
        id: required("id")?,
        scope: required("scope")?,
        conversation: text("conversation")?,
        turn: integer(&map, "turn")?,
        source: text("source")?,
        about: text("about")?,
        about_turn: integer(&map, "about_turn")?,
        actor: required("actor")?,
        body: required("body")?,
        created_at: stamp("created_at")?.ok_or("message field created_at is missing")?,
        due_at: stamp("due_at")?.ok_or("message field due_at is missing")?,
        delivery,
        actionable: flag("actionable")?,
        occurrence: integer(&map, "occurrence")?.unwrap_or(1),
        status: text("status")?.unwrap_or_else(|| "open".to_string()),
        read_by: text("read_by")?,
        read_at: stamp("read_at")?,
        owner: text("owner")?,
        claim_token: text("claim_token")?,
        claim_until: stamp("claim_until")?,
        outcome: text("outcome")?,
        wake_owner: text("wake_owner")?,
        wake_token: text("wake_token")?,
        wake_until: stamp("wake_until")?,
        wake_accepted: flag("wake_accepted")?,
    };
    message_path(&message.id)?;
    check_target(
        &message.scope,
        message.conversation.as_deref(),
        message.turn,
    )?;
    check_target(&message.scope, message.about.as_deref(), message.about_turn)?;
    if let Some(source) = &message.source {
        parse_conversation_id(source).map_err(|refused| refused.message)?;
    }
    if message.occurrence < 1 {
        return Err("message occurrence is not a count".to_string());
    }
    Ok(message)
}

fn integer(map: &Map<String, Value>, key: &str) -> Result<Option<i64>, String> {
    match map.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(number)) => number
            .as_i64()
            .map(Some)
            .ok_or_else(|| format!("message field {key} is not an integer")),
        Some(_) => Err(format!("message field {key} is not an integer")),
    }
}

#[cfg(test)]
mod tests;
