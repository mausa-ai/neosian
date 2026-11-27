//! The handoff assembled turn by turn from reduced turns (§40.54 item 19,
//! the T1fb slice). The newest turns whose blocks fit three quarters of
//! the budget stay blocks, the newest always; each turn before them
//! becomes its log line, or in the default view the projection that
//! covers it, and only the lines some budget could still keep are held,
//! with the first turn the fold line names. In the default view this is
//! `render_continuation`'s arithmetic exactly. The explicit views count
//! the frame and every line break as well and cut an oversized newest
//! block before a pointer to it, so their whole answer stays within the
//! budget.

use std::collections::{HashMap, HashSet, VecDeque};

use super::{CONTINUE_BUDGET_CHARS, prompt};
use crate::conversation::preview::{Builder, Ends, Preview, Supply, Walked, walk};
use crate::conversation::projection::{entry_line, select};
use crate::conversation::recall::View;
use crate::conversation::views::{Line, fit};
use crate::conversation::{Error, Projection};
use crate::state::State;
use crate::state::records::Records;
use crate::text::{head, len};

/// What an explicit view keeps free beside its blocks for the fold line.
const FOLD_ROOM: usize = 96;

/// What reading a conversation for a view found.
pub enum Assembled {
    Read(Box<Assembler>, Ends),
    Empty,
    Unconvertible,
}

/// `target` read in `view` from `records` when the store keeps them,
/// else whole from `store`, and assembled; the default view's
/// projections are read first.
pub fn read(
    store: &dyn State,
    records: Option<&dyn Records>,
    target: &str,
    view: View,
) -> Result<Assembled, Error> {
    let entries = match view {
        View::Light => store.read_projections(target, 0, None)?,
        _ => Vec::new(),
    };
    let supply = match records {
        Some(records) => Supply::Kept(records),
        None => Supply::Whole(store),
    };
    let mut assembler = Assembler::new(view, target, entries);
    let mut builder = Builder::new(view, target);
    let walked = walk(&supply, target, &mut builder, &mut |preview| {
        assembler.push(&preview);
        Ok(())
    })?;
    Ok(match walked {
        Walked::Read(ends) => Assembled::Read(Box::new(assembler), ends),
        Walked::Empty => Assembled::Empty,
        Walked::Unconvertible => Assembled::Unconvertible,
    })
}

/// A turn whose block is still kept: its block, what the block counts
/// for, and the line it becomes when it no longer fits.
struct Held {
    turn: u64,
    block: String,
    len: usize,
    line: String,
}

pub struct Assembler {
    view: View,
    conversation: String,
    entries: Vec<Projection>,
    winner: HashMap<u64, usize>,
    emitted: HashSet<usize>,
    blocks: VecDeque<Held>,
    spent: usize,
    lines: VecDeque<Line>,
    used: usize,
    start: Option<u64>,
}

impl Assembler {
    fn new(view: View, conversation: &str, entries: Vec<Projection>) -> Self {
        Self {
            view,
            conversation: conversation.to_string(),
            winner: select(&entries),
            entries,
            emitted: HashSet::new(),
            blocks: VecDeque::new(),
            spent: 0,
            lines: VecDeque::new(),
            used: 0,
            start: None,
        }
    }

    /// The next turn, oldest first.
    fn push(&mut self, preview: &Preview) {
        let block = preview.block(self.view);
        let len = len(&block) + usize::from(self.view != View::Light);
        self.spent += len;
        self.blocks.push_back(Held {
            turn: preview.turn,
            block,
            len,
            line: preview.line(self.view),
        });
        self.trim(CONTINUE_BUDGET_CHARS * 3 / 4);
    }

    /// The oldest blocks past `share` become older turns, the newest stays.
    fn trim(&mut self, share: usize) {
        while self.blocks.len() > 1 && self.spent > share {
            let held = self.blocks.pop_front().expect("more than one block");
            self.spent -= held.len;
            self.older(held.turn, held.line);
        }
    }

    /// An older turn's line, or the first line of the projection that
    /// covers it; a line no budget can keep keeps only its length, and the
    /// oldest go once the lines after them pass every budget.
    fn older(&mut self, turn: u64, text: String) {
        let mut line = match self.winner.get(&turn) {
            None => Line::new(turn, turn, text),
            Some(&index) if self.emitted.insert(index) => {
                let entry = &self.entries[index];
                let first = entry.turn - entry.span + 1;
                Line::new(first, entry.turn, entry_line(entry))
            }
            Some(_) => return,
        };
        if line.len > CONTINUE_BUDGET_CHARS {
            line.text = String::new();
        }
        self.start.get_or_insert(line.first);
        self.used += line.len + 1;
        self.lines.push_back(line);
        while self.lines.len() > 1 && self.used - (self.lines[0].len + 1) > CONTINUE_BUDGET_CHARS {
            let gone = self.lines.pop_front().expect("more than one line");
            self.used -= gone.len + 1;
        }
    }

    /// The answer: the frame's opening parts, the older turns' lines, the
    /// blocks, the footer.
    pub fn finish(mut self, mut parts: Vec<String>, footer: String) -> String {
        let older = if self.view == View::Light {
            CONTINUE_BUDGET_CHARS.saturating_sub(self.spent).max(1)
        } else {
            let fixed = parts.iter().map(|part| len(part) + 1).sum::<usize>() + len(&footer);
            let content = CONTINUE_BUDGET_CHARS.saturating_sub(fixed);
            let room = content.saturating_sub(FOLD_ROOM);
            self.trim((content * 3 / 4).min(room));
            self.cut_newest(room);
            content.saturating_sub(self.spent).max(1)
        };
        if !self.lines.is_empty() {
            let start = self.start.unwrap_or_default();
            parts.extend(fit(self.lines.into(), start, older));
        }
        parts.extend(self.blocks.into_iter().map(|held| held.block));
        parts.push(footer);
        parts.join("\n")
    }

    /// The newest block, when it passes `room`, cut after its last whole
    /// line that fits, before a line that says how to read the turn.
    fn cut_newest(&mut self, room: usize) {
        if self.spent <= room {
            return;
        }
        let Some(newest) = self.blocks.back_mut() else {
            return;
        };
        let template = prompt("continue_more");
        let turn = newest.turn.to_string();
        let more = crate::assets::render(
            &template,
            &[("turn", &turn), ("conversation_id", &self.conversation)],
        );
        let free = room.saturating_sub(len(&more) + 2);
        let mut kept: Vec<&str> = Vec::new();
        let mut used = 0;
        for line in newest.block.split('\n') {
            let cost = len(line) + usize::from(!kept.is_empty());
            if used + cost > free {
                if kept.is_empty() && free > 0 {
                    kept.push(head(line, free));
                }
                break;
            }
            used += cost;
            kept.push(line);
        }
        kept.push(&more);
        let block = kept.join("\n");
        self.spent -= newest.len;
        newest.len = len(&block) + 1;
        self.spent += newest.len;
        newest.block = block;
    }
}
