//! A whole turn in one of four views (DESIGN §40.54 item 19, #448). Each
//! view reads only the kinds it shows, with heads where it shows less than
//! a piece: `light` prompts and replies (long ones as head and tail), tool
//! calls and results as heads; `prompts` the user prompts whole; `calls`
//! prompts, replies and calls whole and each result as a one-byte head,
//! for its size; `full` the same with each result over 4,096 bytes as head
//! and tail. Reasoning, attachments and extra data are never read. Lines
//! are role-labeled as `recall_turn` has always drawn them; a result's
//! marker names `call` when that call opens this piece, else its `seq`.

use std::collections::HashMap;

use super::light::Light;
use super::origin::{Item, Origin};
use super::pieces::{Gather, Piece};
use super::{Outcome, View, grouped, marker};
use crate::conversation::projection::REDACTED;
use crate::conversation::turn_records::{Kind, RecordRead};
use crate::state::records::Fault;
use crate::text::strip;

/// The kinds a turn's views read; system messages are shown by `seq` only.
const TURN: [Kind; 4] = [Kind::Prompt, Kind::Reply, Kind::ToolCall, Kind::ToolResult];
/// `full`'s head and tail of a result, each.
const FULL_HEAD: usize = 2_048;

/// What `view` asks of the store.
pub fn request(turn: u64, view: View) -> RecordRead {
    let (kinds, heads): (&[Kind], Vec<(Kind, usize)>) = match view {
        View::Light => (&TURN, Light::heads()),
        View::Prompts => (&[Kind::Prompt], Vec::new()),
        View::Calls => (&TURN, vec![(Kind::ToolResult, 1)]),
        View::Full => (&TURN, vec![(Kind::ToolResult, FULL_HEAD)]),
    };
    RecordRead {
        turn: Some(turn),
        kinds: Some(kinds.to_vec()),
        heads,
        ..RecordRead::default()
    }
}

/// Turn `turn` in `view`, read one page at a time.
pub fn render(origin: &Origin, turn: u64, view: View) -> Result<Outcome, Fault> {
    let mut header = None;
    let mut gather = Gather::default();
    let mut light = Light::default();
    let mut whole = Whole::default();
    let mut add = |piece: Piece| match view {
        View::Light => light.add(piece),
        _ => whole.add(piece),
    };
    origin.read(&request(turn, view), &mut |item| {
        match item {
            Item::Header(found) if found.turn == turn => header = Some(found),
            Item::Header(_) => {}
            Item::Record(answered) => {
                if let Some(piece) = gather.push(answered)? {
                    add(piece);
                }
            }
        }
        Ok(true)
    })?;
    if let Some(piece) = gather.finish()? {
        add(piece);
    }
    let Some(header) = header else {
        return Ok(Outcome::Missing);
    };
    if header.redacted {
        return Ok(Outcome::Shown(format!("Turn {turn}: {REDACTED}")));
    }
    let key = match view {
        View::Light => "recall_turn_light",
        View::Prompts => "recall_turn_prompts",
        View::Calls => "recall_turn_calls",
        View::Full => "recall_turn_full",
    };
    let mut lines = vec![marker(key, &[("turn", &turn.to_string())])];
    match view {
        View::Light => light.lines(&mut lines),
        _ => whole.lines(view, &mut lines),
    }
    if lines.len() == 1 {
        lines.push(marker("recall_turn_empty", &[]));
    }
    Ok(Outcome::Shown(lines.join("\n")))
}

/// The marker on a result or a cut piece: `call` when it opens this piece.
pub fn selector(piece: &Piece, opens: impl Fn(&str) -> bool) -> String {
    match piece.call_id.as_deref() {
        Some(id) if !id.is_empty() && piece.kind == Kind::ToolResult && opens(id) => {
            format!("call=\"{id}\"")
        }
        _ => format!("seq={}", piece.seq),
    }
}

/// A piece as shown: whole, or head and tail around the omitted marker.
pub fn shown(piece: &Piece, selector: &str) -> String {
    let Some(tail) = &piece.tail else {
        return piece.text.clone();
    };
    let omitted = marker(
        "recall_turn_omitted",
        &[
            ("bytes", &grouped(piece.omitted())),
            ("size", &grouped(piece.size)),
            ("selector", selector),
        ],
    );
    format!("{}{omitted}{tail}", piece.text)
}

/// The pieces of `prompts`, `calls` and `full`, kept whole as asked.
#[derive(Default)]
struct Whole {
    pieces: Vec<Piece>,
    /// Each call id's tool, the last call naming it.
    names: HashMap<String, String>,
    /// Each call id's last result piece: what `call` opens.
    last: HashMap<String, u64>,
}

impl Whole {
    fn add(&mut self, piece: Piece) {
        if let Some(id) = piece.call_id.clone() {
            match piece.kind {
                Kind::ToolCall => {
                    self.names
                        .insert(id, piece.name.clone().unwrap_or_default());
                }
                Kind::ToolResult => {
                    self.last.insert(id, piece.seq);
                }
                _ => {}
            }
        }
        self.pieces.push(piece);
    }

    fn opens(&self, piece: &Piece) -> impl Fn(&str) -> bool {
        let (names, last, seq) = (&self.names, &self.last, piece.seq);
        move |id| names.contains_key(id) && last.get(id) == Some(&seq)
    }

    fn lines(&self, view: View, out: &mut Vec<String>) {
        let mut at = 0;
        while let Some(first) = self.pieces.get(at) {
            if first.kind == Kind::ToolCall {
                let name = first.name.as_deref().unwrap_or("");
                out.push(format!("AGENT calls {name}({})", first.text));
                at += 1;
                continue;
            }
            let group: Vec<&Piece> = self.pieces[at..]
                .iter()
                .take_while(|p| (p.message, p.kind) == (first.message, first.kind))
                .collect();
            at += group.len();
            let each = |piece: &&Piece| {
                let selector = selector(piece, self.opens(piece));
                match (view, piece.kind) {
                    (View::Calls, Kind::ToolResult) => marker(
                        "recall_turn_result",
                        &[("bytes", &grouped(piece.size)), ("selector", &selector)],
                    ),
                    _ => shown(piece, &selector),
                }
            };
            let text = group.iter().map(each).collect::<Vec<_>>().join("\n");
            match first.kind {
                Kind::Prompt => out.push(format!("USER: {text}")),
                Kind::Reply if !strip(&text).is_empty() => {
                    out.push(format!("AGENT: {}", strip(&text)));
                }
                Kind::ToolResult => {
                    let name = first.call_id.as_deref().and_then(|id| self.names.get(id));
                    let name = name.map_or("tool", String::as_str);
                    out.push(format!("TOOL {name} → {text}"));
                }
                _ => {}
            }
        }
    }
}
