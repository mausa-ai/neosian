//! One turn's pieces reduced as they arrive: each message drawn into the
//! segments a view shows once it ends, links registered in the order the
//! released registry numbers them (a message's text, then its calls'
//! argument strings), each result matched to its calls when the turn
//! ends, the last result with an id winning. Only the turn's last
//! assistant prose keeps its text, since only the final answer is shown
//! verbatim; a piece the read cut keeps a bounded head and tail around a
//! marker that names the record to read it from.

use std::collections::HashMap;

use serde_json::Value;

use super::text::{Text, WIDE_ARGS, escaped, register};
use crate::assets::{CONTEXT, folded, render};
use crate::conversation::handoff::{DIGEST_CHARS, USER_CHARS};
use crate::conversation::links::LinkRegistry;
use crate::conversation::message::Role;
use crate::conversation::projection::{ARGS_CHARS, args_line, one_line};
use crate::conversation::recall::View;
use crate::conversation::recall::pieces::Piece;
use crate::conversation::turn_records::{Header, Kind};
use crate::pyfmt::dumps;
use crate::text::strip;

/// One turn, reduced; its time and actor stay in its header.
pub struct Preview {
    pub turn: u64,
    pub redacted: bool,
    pub said: Vec<Said>,
    /// The one line a turn records cannot carry is shown as.
    pub fixed: Option<String>,
}

/// What a turn said, in provider order.
pub enum Said {
    /// A user prompt: its log segment and its text as a verbatim block
    /// shows it.
    User { line: String, text: String },
    /// An assistant message's prose, when it has any: its log segment, and
    /// its text while it is the turn's last prose.
    Agent { line: String, text: Option<String> },
    /// One tool call of an assistant message.
    Call(Round),
}

/// A tool call and what answered it.
pub struct Round {
    pub name: String,
    /// The arguments as a log segment draws them, and as `calls` and
    /// `full` draw them.
    pub args: String,
    pub wide: String,
    pub outcome: Option<Outcome>,
    id: String,
}

/// A call's result.
#[derive(Clone)]
pub struct Outcome {
    /// The result as a log segment digests it, and as `full` does.
    pub digest: String,
    pub wide: String,
    /// Its text's UTF-8 bytes.
    pub size: u64,
}

struct Message {
    index: u64,
    role: Role,
    texts: Vec<Text>,
    call_id: Option<String>,
    rounds: Vec<Round>,
}

/// The reduction of the turn being read.
pub struct Builder {
    pub(super) view: View,
    pub(super) conversation: String,
    pub(super) links: LinkRegistry,
    said: Vec<Said>,
    /// Where the turn's last prose with its text kept is in `said`.
    prose: Option<usize>,
    results: HashMap<String, Outcome>,
    message: Option<Message>,
}

impl Builder {
    pub fn new(view: View, conversation: &str) -> Self {
        Self {
            view,
            conversation: conversation.to_string(),
            links: LinkRegistry::of(&[], Some(conversation)),
            said: Vec::new(),
            prose: None,
            results: HashMap::new(),
            message: None,
        }
    }

    pub fn view(&self) -> View {
        self.view
    }

    fn wide(&self) -> bool {
        matches!(self.view, View::Calls | View::Full)
    }

    /// The next piece of turn `turn`, in record order.
    pub fn piece(&mut self, turn: u64, piece: Piece) {
        if self
            .message
            .as_ref()
            .is_some_and(|m| m.index != piece.message)
        {
            self.end_message(turn);
        }
        let round = (piece.kind == Kind::ToolCall).then(|| self.round(&piece));
        let message = self.message.get_or_insert_with(|| Message {
            index: piece.message,
            role: piece.role,
            texts: Vec::new(),
            call_id: None,
            rounds: Vec::new(),
        });
        match piece.kind {
            Kind::Prompt | Kind::Reply | Kind::System | Kind::ToolResult => {
                register(&mut self.links, &piece.text, piece.tail.as_deref());
                if piece.kind == Kind::ToolResult {
                    message.call_id = piece.call_id.clone();
                }
                message.texts.push(Text {
                    seq: piece.seq,
                    size: piece.size,
                    tail: piece.tail,
                    text: piece.text,
                });
            }
            // A result held only as an attachment or as extra data is
            // still its call's result.
            Kind::Attachment | Kind::Extra
                if message.role == Role::Tool && message.call_id.is_none() =>
            {
                message.call_id = piece.call_id;
            }
            _ => {}
        }
        if let Some(round) = round.filter(|_| message.role == Role::Assistant) {
            message.rounds.push(round);
        }
    }

    /// The turn `header` names, ended and reduced; the builder is ready
    /// for the next one.
    pub fn finish(&mut self, header: Header) -> Preview {
        self.end_message(header.turn);
        let mut said = std::mem::take(&mut self.said);
        let results = std::mem::take(&mut self.results);
        self.prose = None;
        for entry in &mut said {
            if let Said::Call(round) = entry {
                round.outcome = results.get(&round.id).cloned();
            }
        }
        Preview {
            turn: header.turn,
            redacted: header.redacted,
            said,
            fixed: None,
        }
    }

    /// A turn records cannot carry, as one fixed line that points to
    /// `recall_turn`; nothing of it was read but its header.
    pub fn unconvertible(&mut self, header: Header) -> Preview {
        let turn = header.turn.to_string();
        let mut preview = self.finish(header);
        let template =
            folded(CONTEXT, "continue_unconvertible").expect("context.yaml carries the line");
        let values = [
            ("turn", turn.as_str()),
            ("conversation_id", &self.conversation),
        ];
        preview.fixed = Some(format!("[{turn}] {}", render(&template, &values)));
        preview
    }

    /// A call: its argument strings registered, its segments drawn.
    fn round(&mut self, piece: &Piece) -> Round {
        let parsed = piece
            .tail
            .is_none()
            .then(|| serde_json::from_str::<Value>(&piece.text).ok())
            .flatten();
        let ascii = match parsed {
            Some(value) => {
                self.links.register_strings(&value);
                dumps(&value, true)
            }
            None => {
                register(&mut self.links, &piece.text, piece.tail.as_deref());
                escaped(&piece.text)
            }
        };
        let wide = if self.wide() {
            args_line(&ascii, WIDE_ARGS, &self.links)
        } else {
            String::new()
        };
        Round {
            name: piece.name.clone().unwrap_or_default(),
            args: args_line(&ascii, ARGS_CHARS, &self.links),
            wide,
            outcome: None,
            id: piece.call_id.clone().unwrap_or_default(),
        }
    }

    fn end_message(&mut self, turn: u64) {
        let Some(message) = self.message.take() else {
            return;
        };
        match message.role {
            Role::User => {
                let text = self.verbatim(&message.texts, turn);
                let marker = format!(" … [recall_turn({turn})]");
                let line = one_line(&text, USER_CHARS, Some(&self.links), &marker);
                let line = format!("USER: {line}");
                self.said.push(Said::User { line, text });
            }
            Role::Assistant => {
                let prose = self.verbatim(&message.texts, turn);
                let text = strip(&prose);
                if !text.is_empty() {
                    let line = one_line(text, DIGEST_CHARS, Some(&self.links), " …");
                    if let Some(Said::Agent { text, .. }) =
                        self.prose.and_then(|at| self.said.get_mut(at))
                    {
                        *text = None;
                    }
                    self.prose = Some(self.said.len());
                    self.said.push(Said::Agent {
                        line: format!("AGENT: {line}"),
                        text: Some(text.to_string()),
                    });
                }
                self.said.extend(message.rounds.into_iter().map(Said::Call));
            }
            Role::Tool => {
                if let Some(id) = message.call_id.clone() {
                    let outcome = self.outcome(&message.texts);
                    self.results.insert(id, outcome);
                }
            }
            Role::System => {}
        }
    }
}
