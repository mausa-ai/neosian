//! Numbers inside a JSON piece: the grammar's states as characters land,
//! and the spelling a number must have once it ends (review findings 2 and
//! 10 of 2481787): admitted exactly when the read's own parser takes it,
//! and only as the codec writes that value.

use serde::{Deserialize, Serialize};
use serde_json::Number;

use crate::pyfmt::float_repr;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Num {
    Minus,
    Zero,
    Int,
    Dot,
    Frac,
    E,
    Sign,
    Exp,
}

/// The number state after `ch`, or None when `ch` ends the number.
pub(super) fn next(at: Num, ch: char) -> Option<Num> {
    Some(match (at, ch) {
        (Num::Minus, '0') => Num::Zero,
        (Num::Minus, '1'..='9') | (Num::Int, '0'..='9') => Num::Int,
        (Num::Zero | Num::Int, '.') => Num::Dot,
        (Num::Dot | Num::Frac, '0'..='9') => Num::Frac,
        (Num::Zero | Num::Int | Num::Frac, 'e' | 'E') => Num::E,
        (Num::E, '+' | '-') => Num::Sign,
        (Num::E | Num::Sign | Num::Exp, '0'..='9') => Num::Exp,
        _ => return None,
    })
}

/// Whether a state can end a number.
pub(super) fn whole(at: Num) -> bool {
    matches!(at, Num::Zero | Num::Int | Num::Frac | Num::Exp)
}

/// A complete number's text as the codec writes the value the read takes:
/// serde_json parses it (so a seal never makes a turn the read refuses),
/// and its spelling is the integer's digits, or `float.__repr__` of the
/// correctly rounded float (`pyfmt`, so `1.5`, `1e+16`, `-0.0`, and never
/// `1.50`, `1E2` or `-0`).
pub(super) fn spelled(text: &str) -> Result<(), &'static str> {
    let Ok(read) = serde_json::from_str::<Number>(text) else {
        return Err("a number out of range");
    };
    let codec = if read.is_f64() {
        text.parse::<f64>().ok().map(float_repr)
    } else {
        Some(read.to_string())
    };
    if codec.as_deref() == Some(text) {
        Ok(())
    } else {
        Err("a number not spelled as the codec writes it")
    }
}
