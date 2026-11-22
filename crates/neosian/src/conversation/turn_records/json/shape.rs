//! What a JSON piece must be and how deep it may nest (§40.54 item 9 and
//! the review of 2481787): its shape by kind, the codec's truthiness of a
//! whole value, and the depth a format-1 row holds at the piece's place.

use serde::{Deserialize, Serialize};

use super::super::Kind;

/// The deepest serde_json reads a whole format-1 turn row: its limit of
/// 128 is reached at the 128th open container.
const ROW_DEPTH: usize = 127;

/// The deepest a piece may nest: what a format-1 row holds at the piece's
/// place (review finding 13), so every stored turn converts and every
/// record turn folds back. The levels around it in a row: the row, its
/// messages and the message (a reasoning or a message's extra), with the
/// content list (an attachment), or with the calls and the call (a call's
/// arguments or extra).
pub fn depth(kind: Kind, call: Option<u64>) -> usize {
    let around = match kind {
        Kind::ToolCall => 5,
        Kind::Extra if call.is_some() => 5,
        Kind::Attachment => 4,
        _ => 3,
    };
    ROW_DEPTH - around
}

/// What a piece's value must be.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Shape {
    Any,
    Object,
    Block,
}

impl Shape {
    pub fn of(kind: Kind) -> Option<Self> {
        match kind {
            Kind::ToolCall => Some(Self::Object),
            Kind::Attachment => Some(Self::Block),
            Kind::Reasoning | Kind::Extra => Some(Self::Any),
            _ => None,
        }
    }
}

/// What a complete value was, as the codec's truthiness reads it: it
/// leaves a falsy extra and a null reasoning out.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Top {
    Null,
    Falsy,
    #[default]
    Truthy,
}
