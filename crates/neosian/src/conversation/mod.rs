//! Conversations as the file substrate keeps them (DESIGN §9.8, §32,
//! §33): the turn rows and their messages, the one search rule, the
//! renderings, the sessions vocabulary and the handoff delivery, with the
//! two verbs over them. Reads alone at R1 slice B (#358): the record verb
//! (slice C) and the MCP server (slice D) bring the writes.

pub mod cli_continue;
pub mod cli_search;
pub mod handoff;
pub mod hits;
pub mod ids;
pub mod links;
pub mod message;
pub mod projection;
pub mod rows;
pub mod search;
pub mod sessions;
pub mod views;

use std::io::{self, Write};

use serde_json::json;

use self::message::Message;
use crate::repr::repr;
use crate::store::error::{Error as StoreError, MemoryError};
use crate::store::time::Stamp;

/// `CONVERSATION_FORMAT_VERSION`: the turn format this build reads;
/// anything newer is refused (CS6).
pub const FORMAT: u64 = 1;

/// One append-only turn (`conversation/types.py`): the USER message that
/// opened it and every message the run produced, in provider order; a
/// redacted one keeps the skeleton with no messages (§38).
#[derive(Debug)]
pub struct Turn {
    pub conversation_id: String,
    pub turn: u64,
    pub messages: Vec<Message>,
    pub created_at: Stamp,
    pub actor: Option<String>,
    pub redacted: bool,
}

/// One checkpointed compaction entry (§9.6): `turn` is the last turn it
/// covers, `span` how many consecutive turns end there.
#[derive(Debug)]
pub struct Projection {
    pub turn: u64,
    pub text: String,
    pub span: u64,
}

/// A refusal of the conversation seam: the codes and sentences of
/// `shared/exceptions/conversation.py`, since both doors print them.
#[derive(Debug)]
pub struct Refused {
    pub code: &'static str,
    pub message: String,
}

impl Refused {
    pub fn id_invalid(conversation_id: &str, reason: &str) -> Self {
        Self {
            code: "agent_conversation_id_invalid",
            message: format!(
                "Invalid conversation id {}: {reason}",
                repr(conversation_id)
            ),
        }
    }

    pub fn format_unsupported(conversation_id: &str, reason: &str) -> Self {
        Self {
            code: "agent_conversation_format_unsupported",
            message: format!(
                "Unsupported turn format in conversation {}: {reason}",
                repr(conversation_id)
            ),
        }
    }
}

/// What a read over turns and documents fails with: a refusal of either
/// seam, or the filesystem's own error.
#[derive(Debug)]
pub enum Error {
    Conversation(Refused),
    Memory(MemoryError),
    Io(io::Error),
}

impl Error {
    /// `[code] message`, or the filesystem's own text: what a verb prints
    /// at tier one.
    pub fn text(&self) -> String {
        match self {
            Self::Conversation(refused) => format!("[{}] {}", refused.code, refused.message),
            Self::Memory(error) => format!("[{}] {}", error.code, error.message),
            Self::Io(error) => error.to_string(),
        }
    }
}

impl From<Refused> for Error {
    fn from(refused: Refused) -> Self {
        Self::Conversation(refused)
    }
}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<StoreError> for Error {
    fn from(error: StoreError) -> Self {
        match error {
            StoreError::Memory(error) => Self::Memory(error),
            StoreError::Io(error) => Self::Io(error),
        }
    }
}

/// Tier one on the streams (the verbs' `_failed`): the one object under
/// `--json`, the sentence and its hint on stderr, exit 1.
pub fn failed(
    text: &str,
    hint: Option<&str>,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    if json {
        writeln!(out, "{}", json!({"error": text, "hint": hint}))?;
    }
    writeln!(err, "error: {text}")?;
    if let Some(hint) = hint {
        writeln!(err, "hint: {hint}")?;
    }
    Ok(1)
}
