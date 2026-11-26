//! A Stop whose own turn is held (ruling E10 of the T1e1 plan): it fails
//! with "the turn is held and will not be sent again: <message>" and a hint
//! of its own naming the turn's place under `held/<id>/` in the spool, not
//! the hint about the next Stop that every other failure prints.

use std::fmt;
use std::io;
use std::path::{Path, PathBuf};

use crate::conversation::Error;

/// The failure, carried inside an I/O error so the error type stays the
/// one every verb reports; `hint` finds it again.
#[derive(Debug)]
struct Held {
    message: String,
    turn: PathBuf,
}

impl fmt::Display for Held {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = &self.message;
        write!(f, "the turn is held and will not be sent again: {message}")
    }
}

impl std::error::Error for Held {}

/// The failure of a Stop whose own turn `n` is held in `directory`.
pub fn error(directory: &Path, n: u64, message: &str) -> Error {
    let held = Held {
        message: message.to_string(),
        turn: directory.join(format!("{n}.jsonl")),
    };
    Error::Io(io::Error::other(held))
}

/// The hint of a failure that holds the Stop's own turn; `None` for every
/// other failure, which keeps its caller's hint.
pub fn hint(error: &Error) -> Option<String> {
    let Error::Io(error) = error else {
        return None;
    };
    let held = error.get_ref()?.downcast_ref::<Held>()?;
    let reason = held.turn.with_extension("reason");
    Some(format!(
        "the record never blocks the agent; the turn stays in {} with its reason in {}, \
         and nothing sends it again by itself",
        held.turn.display(),
        reason.display()
    ))
}
