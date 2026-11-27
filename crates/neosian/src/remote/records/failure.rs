//! Why a turn-records call did not answer, typed so a caller branches
//! without reading messages (DESIGN §40.54 item 17.4): the HTTP status, the
//! seconds of `Retry-After`, the envelope's code and its details as sent.

use serde_json::Value;

use crate::store::error::Error;

/// An answer outside 2xx. `code`, `message` and `details` are the
/// envelope's, verbatim; an answer without an envelope has no code, the
/// start of its body as the message and null details.
#[derive(Debug, PartialEq, Eq)]
pub struct Refusal {
    pub status: u16,
    /// `Retry-After` as whole seconds. An HTTP date reads as absent.
    pub retry_after: Option<u64>,
    pub code: Option<String>,
    pub message: String,
    pub details: Value,
}

/// The code whose details name one of the twelve conflict reasons.
#[cfg_attr(not(test), allow(dead_code))]
const CONFLICT: &str = "agent_conversation_conflict";

#[derive(Debug)]
pub enum Failure {
    /// The handshake did not declare `turn_records` version 1. Nothing was
    /// sent; the caller uses the legacy routes.
    Unsupported,
    /// The service answered outside 2xx.
    Refused(Box<Refusal>),
    /// No answer arrived, or it broke off: connection, timeout, TLS. The
    /// request may or may not have been applied.
    Transport(Error),
    /// A 2xx answer that is not the route's answer. The request was
    /// applied if it was a write.
    Unreadable(String),
    /// This side could not ask or could not take the answer: a credential
    /// that cannot be read, a setting out of range, a 2xx answer past
    /// `NEOSIAN_MAX_RESPONSE_BYTES`, or the caller's own page visitor.
    Client(Error),
}

impl Failure {
    /// A refusal from the status, the header and whatever of the body came.
    /// A body that is not an envelope keeps its first 256 scalars as the
    /// message, read as UTF-8 with replacements, so a gateway's page can be
    /// shown without being held.
    pub(super) fn refused(status: u16, retry_after: Option<&str>, body: &[u8]) -> Self {
        let envelope = serde_json::from_slice::<Value>(body)
            .ok()
            .and_then(|mut data| data.get_mut("error").map(Value::take))
            .filter(Value::is_object);
        let text = |value: Option<&Value>| value.and_then(Value::as_str).map(str::to_string);
        let (code, message, details) = match envelope {
            Some(mut error) => (
                text(error.get("code")),
                text(error.get("message")).unwrap_or_default(),
                error
                    .get_mut("details")
                    .map(Value::take)
                    .unwrap_or_default(),
            ),
            None => {
                let shown = String::from_utf8_lossy(body);
                (None, shown.chars().take(256).collect(), Value::Null)
            }
        };
        Self::Refused(Box::new(Refusal {
            status,
            retry_after: retry_after.and_then(seconds),
            code,
            message,
            details,
        }))
    }

    /// The reason of a conflict (`draft_gap`, `restore_open` and the rest).
    /// Other codes carry a free-text `reason` in their details; that is
    /// not answered here. The lander reads the same rule from a `Refusal`,
    /// a local store's or a server's (`record/landing/rules.rs`).
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn reason(&self) -> Option<&str> {
        match self {
            Self::Refused(refusal) if refusal.code.as_deref() == Some(CONFLICT) => {
                refusal.details.get("reason").and_then(Value::as_str)
            }
            _ => None,
        }
    }
}

/// `Retry-After` as delay seconds: ASCII digits only.
fn seconds(header: &str) -> Option<u64> {
    let header = header.trim();
    let digits = !header.is_empty() && header.bytes().all(|byte| byte.is_ascii_digit());
    digits.then(|| header.parse().ok()).flatten()
}

impl std::fmt::Display for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unsupported => f.write_str("the server does not declare turn records version 1"),
            Self::Refused(refusal) => {
                let code = refusal.code.as_deref().unwrap_or("no envelope");
                let (status, message) = (refusal.status, &refusal.message);
                write!(f, "refused with {status} ({code}): {message}")
            }
            Self::Transport(error) => write!(f, "no answer: {error:?}"),
            Self::Unreadable(what) => write!(f, "malformed wire response: {what}"),
            Self::Client(error) => write!(f, "{error:?}"),
        }
    }
}
