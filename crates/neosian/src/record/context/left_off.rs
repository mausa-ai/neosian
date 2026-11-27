//! "Where we left off" (DESIGN §21.7, §33; §40.54 item 19, #448): the
//! scope's chosen sessions, each turn as its log line, the projection that
//! covers it winning, the oldest folded under an even share of the budget.
//! On a store that keeps records each session is read as `continue_session`
//! previews it (`conversation/preview.rs`): a header read for its last
//! turn, then one page and one turn at a time through that turn, heads
//! only, with no reasoning, extra data or attachment body; only the lines
//! the block's budget could keep are held. So that a start stays within its
//! hook budget, the block reads at most `READ_BYTES` of stored turns, shared
//! evenly by the sessions it shows, each from its newest turn back; the
//! turns before that are folded into the count line unread. Any other
//! store, and a session records cannot carry, is read whole and drawn as
//! released.

use std::cmp::Reverse;

use super::{COMPACT_SOURCE, prompt};
use crate::assets::render;
use crate::conversation::handoff::{DIGEST_CHARS, USER_CHARS};
use crate::conversation::links::LinkRegistry;
use crate::conversation::preview::{Builder, Walked, walk_newest};
use crate::conversation::recall::View;
use crate::conversation::sessions::SESSIONS_PREFIX;
use crate::conversation::views::{Older, project_conversation};
use crate::conversation::{Error, Turn};
use crate::state::State;
use crate::state::records::Records;
use crate::store::Entry;
use crate::text::len;

const RECENT_SESSIONS: usize = 3;
/// The stored bytes of the turns one block reads from records, at most.
const READ_BYTES: u64 = 4 << 20;

/// What the start blocks read: the state, and its records while the store
/// keeps them.
#[derive(Clone, Copy)]
pub struct History<'a> {
    pub state: &'a dyn State,
    pub records: Option<&'a dyn Records>,
}

impl<'a> History<'a> {
    pub fn new(state: &'a dyn State, records: Option<&'a dyn Records>) -> Self {
        Self { state, records }
    }
}

/// A chosen session as read: its turns whole, or its log lines from
/// records with the actor of its last turn.
enum Session {
    Whole(Vec<Turn>),
    Kept { actor: Option<String>, older: Older },
}

impl Session {
    fn actor(&self) -> Option<&str> {
        match self {
            Self::Whole(turns) => turns.last().and_then(|turn| turn.actor.as_deref()),
            Self::Kept { actor, .. } => actor.as_deref(),
        }
    }

    fn lines(self, store: &dyn State, id: &str, share: usize) -> Result<Vec<String>, Error> {
        Ok(match self {
            Self::Whole(turns) => {
                let entries = store.read_projections(id, 0, None)?;
                let links = LinkRegistry::of(&turns, Some(id));
                project_conversation(&turns, &entries, DIGEST_CHARS, USER_CHARS, share, &links)
            }
            Self::Kept { older, .. } => older.fit(share),
        })
    }
}

/// The conversations to project, newest first: the own session alone
/// after a compaction (when it is recorded), else the most recently
/// written sessions documents, a redacted one never.
pub fn choose_sessions(entries: &[Entry], own: &str, source: &str) -> Vec<String> {
    let mut listed: Vec<&Entry> = entries
        .iter()
        .filter(|entry| entry.path.starts_with(SESSIONS_PREFIX) && !entry.redacted)
        .collect();
    listed.sort_by_key(|entry| Reverse(entry.updated_at.instant()));
    let ids: Vec<String> = listed
        .iter()
        .map(|entry| entry.path[SESSIONS_PREFIX.len()..].to_string())
        .collect();
    if source == COMPACT_SOURCE && ids.iter().any(|id| id == own) {
        return vec![own.to_string()];
    }
    ids.into_iter().take(RECENT_SESSIONS).collect()
}

/// `id` from records, its newest turns within `bytes` of them, as log
/// lines a budget of `budget` could keep; None when it has no turns.
fn kept(
    store: &dyn State,
    records: &dyn Records,
    id: &str,
    (budget, bytes): (usize, u64),
) -> Result<Option<Session>, Error> {
    let mut older = Older::new(store.read_projections(id, 0, None)?, budget);
    let mut builder = Builder::new(View::Light, id);
    let mut lines = Vec::new();
    let (walked, skipped) = walk_newest(records, id, &mut builder, bytes, &mut |preview| {
        lines.push((preview.turn, preview.line(View::Light)));
        Ok(())
    })?;
    if let Some(skipped) = skipped {
        older.skip(skipped.first, skipped.last);
    }
    for (turn, line) in lines {
        older.push(turn, line);
    }
    Ok(match walked {
        Walked::Read(ends) => Some(Session::Kept {
            actor: ends.last.actor,
            older,
        }),
        Walked::Empty => None,
        Walked::Unconvertible => whole(store, id)?,
    })
}

fn whole(store: &dyn State, id: &str) -> Result<Option<Session>, Error> {
    let turns = store.read_turns(id, 0, None)?;
    Ok((!turns.is_empty()).then_some(Session::Whole(turns)))
}

/// The "where we left off" block, at most `budget_chars` long: every
/// chosen session as log lines under an even share of what the frame
/// lines leave (the fold line says what is hidden).
pub fn render_left_off(
    history: History,
    scope: &str,
    own: &str,
    source: &str,
    budget_chars: isize,
) -> Result<String, Error> {
    let store = history.state;
    let entries = store.list_documents(scope, SESSIONS_PREFIX)?;
    let (header, footer) = (prompt("start_header"), prompt("start_footer"));
    let chosen = choose_sessions(&entries, own, source);
    let most = budget_chars.max(1) as usize;
    let bytes = READ_BYTES / chosen.len().max(1) as u64;
    let mut sessions = Vec::new();
    for conversation_id in chosen {
        let session = match history.records {
            Some(records) => kept(store, records, &conversation_id, (most, bytes))?,
            None => whole(store, &conversation_id)?,
        };
        if let Some(session) = session {
            sessions.push((conversation_id, session));
        }
    }
    let heads: Vec<String> = sessions
        .iter()
        .map(|(conversation_id, session)| {
            let actor = session.actor().filter(|actor| !actor.is_empty());
            render(
                &prompt("start_session"),
                &[
                    ("conversation_id", conversation_id),
                    ("actor", actor.unwrap_or("-")),
                ],
            )
        })
        .collect();
    let chars = |text: &str| len(text) as isize;
    let frames = chars(&header)
        + chars(&footer)
        + heads.iter().map(|head| chars(head) + 1).sum::<isize>()
        + 2;
    let share = (budget_chars - frames)
        .div_euclid(sessions.len().max(1) as isize)
        .max(1) as usize;
    let mut blocks = Vec::new();
    for (head, (conversation_id, session)) in heads.iter().zip(sessions) {
        let mut block = vec![head.clone()];
        block.extend(session.lines(store, &conversation_id, share)?);
        blocks.push(block.join("\n"));
    }
    let body = if blocks.is_empty() {
        prompt("start_empty")
    } else {
        blocks.join("\n")
    };
    Ok([header, body, footer].join("\n"))
}

#[cfg(test)]
mod tests;
