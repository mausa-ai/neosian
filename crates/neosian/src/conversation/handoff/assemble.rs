//! The handoff assembled turn by turn from reduced turns (§40.54 item 19,
//! the T1fb slice). The newest turns whose blocks fit three quarters of
//! the budget stay blocks, the newest always; each turn before them
//! becomes its log line, or in the default view the projection that
//! covers it, and only the lines some budget could still keep are held,
//! with the first turn the fold line names. In the default view this is
//! `render_continuation`'s arithmetic exactly, but for a block's ceiling:
//! a block is drawn only while it stays within `NEWEST_CHARS`, then cut
//! before a pointer to its turn, so a newest turn of many long prompts is
//! never printed whole. The explicit views count the frame and every line
//! break as well and cut an oversized newest block before a pointer to it,
//! so their whole answer stays within the budget.

use std::collections::VecDeque;

use super::{CONTINUE_BUDGET_CHARS, prompt};
use crate::conversation::preview::{Builder, Ends, Preview, Supply, Walked, walk};
use crate::conversation::recall::View;
use crate::conversation::views::Older;
use crate::conversation::{Error, Projection};
use crate::state::State;
use crate::state::records::Records;
use crate::text::{head, len};

/// What an explicit view keeps free beside its blocks for the fold line.
const FOLD_ROOM: usize = 96;
/// The most a block of the default view holds, in characters: a prompt
/// read whole (twice its head of 32,768 bytes) with room for its turn's
/// other lines, so a turn the released view shows whole stays whole.
const NEWEST_CHARS: usize = 2 * 32_768 + 8_192;

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
        Some(records) => Supply::Kept {
            records,
            state: store,
        },
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
    blocks: VecDeque<Held>,
    spent: usize,
    older: Older,
}

impl Assembler {
    fn new(view: View, conversation: &str, entries: Vec<Projection>) -> Self {
        Self {
            view,
            conversation: conversation.to_string(),
            blocks: VecDeque::new(),
            spent: 0,
            older: Older::new(entries, CONTINUE_BUDGET_CHARS),
        }
    }

    /// The next turn, oldest first.
    fn push(&mut self, preview: &Preview) {
        let block = match self.view {
            View::Light => self.bounded(preview),
            _ => preview.block(self.view),
        };
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
            self.older.push(held.turn, held.line);
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
        if !self.older.is_empty() {
            parts.extend(self.older.fit(older));
        }
        parts.extend(self.blocks.into_iter().map(|held| held.block));
        parts.push(footer);
        parts.join("\n")
    }

    /// The line that says how to read turn `turn`.
    fn more(&self, turn: u64) -> String {
        let template = prompt("continue_more");
        let turn = turn.to_string();
        crate::assets::render(
            &template,
            &[("turn", &turn), ("conversation_id", &self.conversation)],
        )
    }

    /// A default view's block within `NEWEST_CHARS`: when the turn passes
    /// it, its lines that fit before the line that says how to read it.
    fn bounded(&self, preview: &Preview) -> String {
        let more = self.more(preview.turn);
        let free = NEWEST_CHARS - len(&more) - 1;
        match preview.block_within(View::Light, free) {
            (block, false) => block,
            (block, true) => format!("{block}\n{more}"),
        }
    }

    /// The newest block, when it passes `room`, cut after its last whole
    /// line that fits, before a line that says how to read the turn.
    fn cut_newest(&mut self, room: usize) {
        if self.spent <= room {
            return;
        }
        let Some(turn) = self.blocks.back().map(|newest| newest.turn) else {
            return;
        };
        let more = self.more(turn);
        let newest = self.blocks.back_mut().expect("a newest block");
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
