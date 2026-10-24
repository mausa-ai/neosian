//! The memory ladder every transport shares (`memory/dispatch.py`, DESIGN
//! §8): one wiring, one shape of answer, and the store-error mapping that
//! runs once, so the shell, the MCP server and the function tool cannot
//! drift. A store refusal never escapes: it becomes `[code] message` with
//! the code's guidance.

use std::io;

use super::commands;
use super::mounts::{Mount, resolve, state_path};
use crate::state::State;
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::Actor;
use std::sync::Arc;

const MAILBOX: &str =
    "Use messages for mailbox lifecycle changes; operator redaction remains available.";

/// The memory wiring of one invocation: a store, its explicit mounts and
/// who is writing.
pub struct Memory {
    pub store: Arc<dyn State>,
    pub mounts: Vec<Mount>,
    pub actor: Actor,
}

/// One memory command with its arguments: Anthropic's vocabulary over one
/// virtual path space.
pub enum Call<'a> {
    View {
        path: &'a str,
        range: Option<(i64, i64)>,
    },
    Create {
        path: &'a str,
        content: &'a str,
    },
    StrReplace {
        path: &'a str,
        old: &'a str,
        new: &'a str,
    },
    Insert {
        path: &'a str,
        line: i64,
        text: &'a str,
    },
    Delete {
        path: &'a str,
    },
    Rename {
        old: &'a str,
        new: &'a str,
    },
}

/// The structured twin of a successful mutation's prose: the canonical
/// virtual path (a rename's destination) and the version row the command
/// appended, which is the number an undo passes back. In-process only: no
/// envelope ever carries it (ledger #98).
#[derive(Debug, PartialEq, Eq)]
pub struct Receipt {
    pub path: String,
    pub version: u64,
    /// A rename's source.
    pub previous_path: Option<String>,
}

/// What a command answers: the data or the error, and guidance for
/// whoever asked (a hint, a caveat, the way to recover).
#[derive(Debug, PartialEq, Eq)]
pub struct Outcome {
    pub result: Result<String, String>,
    pub reminder: Option<String>,
    pub receipt: Option<Receipt>,
}

impl Outcome {
    pub fn ok(data: impl Into<String>) -> Self {
        Self {
            result: Ok(data.into()),
            reminder: None,
            receipt: None,
        }
    }

    pub fn fail(error: impl Into<String>) -> Self {
        Self {
            result: Err(error.into()),
            reminder: None,
            receipt: None,
        }
    }

    pub fn hint(mut self, reminder: impl Into<String>) -> Self {
        self.reminder = Some(reminder.into());
        self
    }

    /// The receipt of a write at `path` (the destination, for a move from
    /// `previous_path`).
    pub fn wrote(mut self, path: String, version: u64, previous_path: Option<String>) -> Self {
        self.receipt = Some(Receipt {
            path,
            version,
            previous_path,
        });
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

/// Execute one command against the mounts. The mailbox and its links are
/// a lifecycle's documents: a command that would write one is turned away
/// before anything runs. A store refusal comes back corrective; only the
/// filesystem's own failure is left to the caller.
pub fn dispatch(memory: &Memory, call: &Call) -> io::Result<Outcome> {
    let answered = || {
        let written = match *call {
            Call::View { .. } => vec![],
            Call::Rename { old, new } => vec![old, new],
            Call::Create { path, .. }
            | Call::StrReplace { path, .. }
            | Call::Insert { path, .. }
            | Call::Delete { path } => vec![path],
        };
        for path in written {
            if state_path(resolve(&memory.mounts, path)?.1) {
                return Ok(Outcome::fail(MAILBOX));
            }
        }
        match *call {
            Call::View { path, range } => commands::view(memory, path, range),
            Call::Create { path, content } => commands::create(memory, path, content),
            Call::StrReplace { path, old, new } => commands::str_replace(memory, path, old, new),
            Call::Insert { path, line, text } => commands::insert(memory, path, line, text),
            Call::Delete { path } => commands::delete(memory, path),
            Call::Rename { old, new } => commands::rename(memory, old, new),
        }
    };
    match answered() {
        Ok(mut outcome) => {
            if let Some(profile) = memory.store.profile() {
                let path = match call {
                    Call::View { path, .. }
                    | Call::Create { path, .. }
                    | Call::StrReplace { path, .. }
                    | Call::Insert { path, .. }
                    | Call::Delete { path } => path,
                    Call::Rename { new, .. } => new,
                };
                if let Ok((mount, _)) = resolve(&memory.mounts, path)
                    && let Some(source) = profile.mounts.iter().find(|m| m.scope == mount.scope)
                {
                    let reference = format!(
                        "workspace={} source={} scope={}",
                        profile.workspace_id, source.source, mount.scope
                    );
                    let hint = outcome.reminder.get_or_insert_with(String::new);
                    if !hint.is_empty() {
                        hint.push('\n');
                    }
                    hint.push_str(&reference);
                }
            }
            Ok(outcome)
        }
        Err(Error::Memory(error)) => Ok(corrective(&error)),
        Err(Error::Value(sentence)) => Ok(Outcome::fail(sentence)),
        Err(Error::Io(error)) => Err(error),
    }
}
