//! The memory ladder every transport shares (`memory/dispatch.py`, DESIGN
//! §8): one wiring, one shape of answer, and the store-error mapping that
//! runs once, so the shell, the MCP server and the function tool cannot
//! drift. A store refusal never escapes: it becomes `[code] message` with
//! the code's guidance.

use std::io;

use super::mounts::Mount;
use crate::store::FileStore;
use crate::store::error::{Error, MemoryError};

/// The memory wiring of one invocation: a store and its explicit mounts.
pub struct Memory {
    pub store: FileStore,
    pub mounts: Vec<Mount>,
}

/// What a command answers: the data or the error, and guidance for
/// whoever asked (a hint, a caveat, the way to recover).
#[derive(Debug, PartialEq, Eq)]
pub struct Outcome {
    pub result: Result<String, String>,
    pub reminder: Option<String>,
}

impl Outcome {
    pub fn ok(data: impl Into<String>) -> Self {
        Self {
            result: Ok(data.into()),
            reminder: None,
        }
    }

    pub fn fail(error: impl Into<String>) -> Self {
        Self {
            result: Err(error.into()),
            reminder: None,
        }
    }

    pub fn hint(mut self, reminder: impl Into<String>) -> Self {
        self.reminder = Some(reminder.into());
        self
    }
}

/// The store-error mapping every transport shows: the code, the message
/// and per-code guidance, never a raise.
pub fn corrective(error: &MemoryError) -> Outcome {
    let outcome = Outcome::fail(format!("[{}] {}", error.code, error.message));
    let hint = match error.code {
        "memory_document_not_found" => {
            "Run the memory tool with command 'view' and path '/' to see what exists."
        }
        "memory_path_invalid" => {
            "Paths look like /mount/topic-name; check the index with view '/'."
        }
        "memory_read_only_mount" => {
            "This mount is reference-only; write to a writable mount instead."
        }
        "memory_edit_only_mount" => {
            "This mount's document set is fixed; update its existing documents instead of \
             creating, deleting, or renaming."
        }
        "memory_conflict" => "The document changed underneath you — view it again before editing.",
        "memory_format_unsupported" => {
            "This document was written by a newer neosian; leave it untouched."
        }
        _ => return outcome,
    };
    outcome.hint(hint)
}

/// A command's answer with its store refusals made corrective; only the
/// filesystem's own failure is left to the caller.
pub fn settle(result: Result<Outcome, Error>) -> io::Result<Outcome> {
    match result {
        Ok(outcome) => Ok(outcome),
        Err(Error::Memory(error)) => Ok(corrective(&error)),
        Err(Error::Io(error)) => Err(error),
    }
}
