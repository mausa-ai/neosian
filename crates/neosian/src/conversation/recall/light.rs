//! The light view (DESIGN §40.54 item 19, #448; the T1f plan's ruling on
//! how much an agent is shown): prompts and replies verbatim, a long one
//! as head and tail around its marker, and each tool call as one line, the
//! line `continue_session` draws for a tool round, ending with the size of
//! the result and the `call` that opens it. A result no call of the turn
//! names is a line of its own. The whole answer, its title and its last
//! line included, stays within `LIGHT_BYTES`: the lines stop where the
//! room left for that last line begins, and the last line names the record
//! the turn goes on from. While the read runs,
//! what is held stays near that ceiling: past it only the results of the
//! calls already held are kept, each as its digest.

use std::collections::{HashMap, HashSet};

use super::pieces::Piece;
use super::view::shown;
use super::{grouped, marker};
use crate::conversation::projection::{args_digest, piece_digest};
use crate::conversation::turn_records::Kind;
use crate::text::strip;

/// The most the light lines of one turn take, in bytes.
pub const LIGHT_BYTES: usize = 65_536;
/// Heads, each of head and tail: a prompt or reply up to twice this is
/// whole; a call's arguments and a result only need a line's worth.
const PROSE_HEAD: usize = 16_384;
const ARGS_HEAD: usize = 1_024;
const RESULT_HEAD: usize = 2_048;
/// What a call line's fixed words and marker may take, beside its name,
/// id and arguments, while the read is held to the ceiling.
const LINE_WORDS: usize = 1_024;

#[derive(Clone)]
struct Digest {
    seq: u64,
    size: u64,
    text: String,
}

enum Entry {
    Prose {
        message: u64,
        user: bool,
        fragments: Vec<(u64, String)>,
    },
    Call {
        seq: u64,
        id: String,
        name: String,
        args: String,
    },
    /// A result whose call the read had not met yet.
    Result { id: Option<String>, digest: Digest },
}

#[derive(Default)]
pub struct Light {
    entries: Vec<Entry>,
    held: usize,
    /// The first record not held.
    cut: Option<u64>,
    /// Every call id of the turn and its tool.
    names: HashMap<String, String>,
    /// The call ids held as lines.
    lines: HashSet<String>,
    /// Each held call id's last result.
    results: HashMap<String, Digest>,
}

impl Light {
    pub fn heads() -> Vec<(Kind, usize)> {
        vec![
            (Kind::Prompt, PROSE_HEAD),
            (Kind::Reply, PROSE_HEAD),
            (Kind::ToolCall, ARGS_HEAD),
            (Kind::ToolResult, RESULT_HEAD),
        ]
    }

    fn hold(&mut self, seq: u64, bytes: usize) -> bool {
        if self.cut.is_none() {
            self.held += bytes;
            if self.held > LIGHT_BYTES {
                self.cut = Some(seq);
            }
        }
        self.cut.is_none()
    }

    pub fn add(&mut self, piece: Piece) {
        match piece.kind {
            Kind::ToolCall => {
                let id = piece.call_id.clone().unwrap_or_default();
                let name = piece.name.clone().unwrap_or_default();
                self.names.insert(id.clone(), name.clone());
                let args = args_digest(&piece.text, piece.tail.is_some());
                if self.hold(piece.seq, LINE_WORDS + id.len() + name.len() + args.len()) {
                    self.lines.insert(id.clone());
                    let seq = piece.seq;
                    self.entries.push(Entry::Call {
                        seq,
                        id,
                        name,
                        args,
                    });
                }
            }
            Kind::ToolResult => {
                let id = piece.call_id.clone();
                let met = id.as_ref().is_some_and(|id| self.names.contains_key(id));
                let held = id.as_ref().is_some_and(|id| self.lines.contains(id));
                if self.cut.is_some() && !held {
                    return;
                }
                let text = piece_digest(&piece.text, piece.tail.as_deref());
                let (seq, size) = (piece.seq, piece.size);
                let digest = Digest { seq, size, text };
                if let Some(id) = &id {
                    self.results.insert(id.clone(), digest.clone());
                }
                if !met && self.hold(seq, LINE_WORDS + digest.text.len()) {
                    self.entries.push(Entry::Result { id, digest });
                }
            }
            Kind::Prompt | Kind::Reply => {
                let fragment = shown(&piece, &format!("seq={}", piece.seq));
                if !self.hold(piece.seq, fragment.len() + 8) {
                    return;
                }
                let (message, user) = (piece.message, piece.kind == Kind::Prompt);
                match self.entries.last_mut() {
                    Some(Entry::Prose {
                        message: m,
                        user: u,
                        fragments,
                    }) if (*m, *u) == (message, user) => fragments.push((piece.seq, fragment)),
                    _ => self.entries.push(Entry::Prose {
                        message,
                        user,
                        fragments: vec![(piece.seq, fragment)],
                    }),
                }
            }
            _ => {}
        }
    }

    fn result(digest: &Digest, selector: &str) -> String {
        let marker = marker(
            "recall_turn_result",
            &[("bytes", &grouped(digest.size)), ("selector", selector)],
        );
        format!("{} {marker}", digest.text)
    }

    /// The lines within `room` bytes, each with its line break, then
    /// where the turn goes on; that last line's room is kept free.
    pub fn lines(&self, out: &mut Vec<String>, room: usize) {
        let end = |seq: &str| {
            let limit = grouped(LIGHT_BYTES as u64);
            marker("recall_turn_light_end", &[("limit", &limit), ("seq", seq)])
        };
        let ceiling = room.saturating_sub(end(&u64::MAX.to_string()).len() + 1);
        let mut used = 0;
        let mut stop = None;
        for entry in &self.entries {
            let (seq, line) = match entry {
                Entry::Prose {
                    user, fragments, ..
                } => {
                    let label = if *user { "USER: " } else { "AGENT: " };
                    let mut text = String::new();
                    for (seq, fragment) in fragments {
                        let joint = usize::from(!text.is_empty());
                        if used + label.len() + text.len() + joint + fragment.len() + 1 > ceiling {
                            stop = Some(*seq);
                            break;
                        }
                        if joint == 1 {
                            text.push('\n');
                        }
                        text.push_str(fragment);
                    }
                    let text = if *user { text.as_str() } else { strip(&text) };
                    if !text.is_empty() || *user && stop.is_none() {
                        let line = format!("{label}{text}");
                        used += line.len() + 1;
                        out.push(line);
                    }
                    if stop.is_some() {
                        break;
                    }
                    continue;
                }
                Entry::Call {
                    seq,
                    id,
                    name,
                    args,
                } => {
                    let outcome = match self.results.get(id) {
                        Some(digest) if id.is_empty() => {
                            Self::result(digest, &format!("seq={}", digest.seq))
                        }
                        Some(digest) => Self::result(digest, &format!("call=\"{id}\"")),
                        None => "?".to_string(),
                    };
                    (*seq, format!("TOOL {name}({args}) → {outcome}"))
                }
                Entry::Result { id, digest } => {
                    if id.as_ref().is_some_and(|id| self.names.contains_key(id)) {
                        continue;
                    }
                    let line = Self::result(digest, &format!("seq={}", digest.seq));
                    (digest.seq, format!("TOOL tool → {line}"))
                }
            };
            if used + line.len() + 1 > ceiling {
                stop = Some(seq);
                break;
            }
            used += line.len() + 1;
            out.push(line);
        }
        if let Some(seq) = stop.or(self.cut) {
            out.push(end(&seq.to_string()));
        }
    }
}
