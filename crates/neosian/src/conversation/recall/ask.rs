//! What a recall asks (DESIGN §40.54 item 19; the T1f plan's ruling on
//! the CLI grammar): a turn shown in a view, or one part of one piece,
//! named by a tool call's id or by a record of the turn. The MCP door and
//! the CLI check their inputs by these rules alone.

use super::{Corrective, hint};

/// How a whole turn is shown.
#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum View {
    Light,
    Prompts,
    Calls,
    Full,
}

impl View {
    pub fn parse(text: &str) -> Option<Self> {
        match text {
            "light" => Some(Self::Light),
            "prompts" => Some(Self::Prompts),
            "calls" => Some(Self::Calls),
            "full" => Some(Self::Full),
            _ => None,
        }
    }
}

/// What opens one piece: a tool call's result, or the piece holding a
/// record of the turn.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Selector {
    Call(String),
    Seq(u64),
}

/// One recall, checked.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Ask {
    pub conversation: String,
    pub turn: u64,
    pub view: View,
    /// One part of a piece, counted from 0, instead of a view.
    pub part: Option<(Selector, u64)>,
}

impl Ask {
    /// The request, or the corrective failure for what it cannot mean: a
    /// turn below 1, both selectors, a negative record or part, a part
    /// without a selector, a view beside a selector.
    pub fn new(
        conversation: &str,
        turn: i64,
        view: Option<View>,
        call: Option<&str>,
        seq: Option<i64>,
        part: Option<i64>,
    ) -> Result<Self, Corrective> {
        let refuse = |error: String, key: &str| Err(Corrective::new(error, Some(hint(key))));
        if turn < 1 {
            return refuse(
                format!("Turn numbers start at 1; got {turn}"),
                "recall_turn_numbers_hint",
            );
        }
        if call.is_some() && seq.is_some() {
            return refuse(
                "Pass call or seq, not both".into(),
                "recall_turn_selectors_hint",
            );
        }
        if let Some(seq) = seq.filter(|seq| *seq < 0) {
            return refuse(
                format!("seq must be at least 0; got {seq}"),
                "recall_turn_selectors_hint",
            );
        }
        if let Some(part) = part.filter(|part| *part < 0) {
            return refuse(
                format!("part must be at least 0; got {part}"),
                "recall_turn_parts_hint",
            );
        }
        let selector = match (call, seq) {
            (Some(call), _) => Some(Selector::Call(call.to_string())),
            (None, Some(seq)) => Some(Selector::Seq(seq as u64)),
            (None, None) => None,
        };
        let part = match selector {
            None if part.is_some() => {
                return refuse("part needs call or seq".into(), "recall_turn_parts_hint");
            }
            Some(_) if view.is_some() => {
                return refuse(
                    "view shows a whole turn; call and seq open one part of it".into(),
                    "recall_turn_view_hint",
                );
            }
            Some(selector) => Some((selector, part.unwrap_or(0) as u64)),
            None => None,
        };
        Ok(Self {
            conversation: conversation.to_string(),
            turn: turn as u64,
            view: view.unwrap_or(View::Light),
            part,
        })
    }
}
