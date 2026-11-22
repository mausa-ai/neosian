//! T1a tests (DESIGN §40.54 items 3, 12 and 14). Literal expectations are
//! written by hand from the design, never generated from this code; the
//! digests in them were computed by an independent SHA-256 over the
//! hand-written canonical lines.

mod bounds;
mod canonical;
mod cuts;
mod drafts;
mod example;
mod facts;
mod generate;
mod json;
mod kinds;
mod properties;
mod refusals;
mod restore_stream;
mod restores;
mod search;
mod views;

use serde_json::Value;

use crate::conversation::message::{self, Message};
use crate::pyfmt::dumps;

/// A message as a FileStore format-1 turn line spells it.
fn spelled(message: &Message) -> String {
    dumps(&message::to_json(message), true)
}

fn message(value: &Value) -> Message {
    message::from_json(value).expect("a message the codec reads")
}
