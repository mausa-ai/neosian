//! The eraser over the turn substrate (`conversation/file_erasure.py`,
//! `erasable.py`, DESIGN §38): the one rewrite path of a conversation. The
//! selected rows are replaced by their skeletons, every projection entry
//! covering one loses its text, both land atomically and synced, and one
//! act goes on the conversation's own trail; the trail read walks the
//! conversation directories the search scan walks.

use std::path::Path;

use super::FileStore;
use super::fileio::{append_line, atomic_write};
use super::time::Stamp;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rows::{
    CONVERSATIONS, PROJECTIONS, REDACTIONS, TURNS, parse_projection, parse_redaction, parse_turn,
    render_projection, render_redaction, render_turn,
};
use crate::conversation::search::held_ids;
use crate::conversation::{Error, Projection, Redaction};

/// `parse_selection`: at most one selector, every number from 1, the
/// named turns deduplicated and ascending.
fn selection(
    through: Option<i64>,
    turns: Option<&[i64]>,
) -> Result<(Option<u64>, Option<Vec<u64>>), Error> {
    if through.is_some() && turns.is_some() {
        return Err(Error::Value(
            "give through= or turns=, not both".to_string(),
        ));
    }
    if let Some(through) = through.filter(|through| *through < 1) {
        return Err(Error::Value(format!("through must be >= 1, got {through}")));
    }
    let Some(turns) = turns else {
        return Ok((through.map(|through| through as u64), None));
    };
    let mut named: Vec<i64> = turns.to_vec();
    named.sort_unstable();
    named.dedup();
    if let Some(first) = named.first().filter(|first| **first < 1) {
        return Err(Error::Value(format!(
            "turn numbers start at 1, got {first}"
        )));
    }
    Ok((
        None,
        Some(named.into_iter().map(|turn| turn as u64).collect()),
    ))
}

fn selected(number: u64, through: Option<u64>, named: Option<&[u64]>) -> bool {
    match named {
        Some(named) => named.contains(&number),
        None => through.is_none_or(|through| number <= through),
    }
}

/// `covers`: whether the entry's range holds any of the numbers.
fn covers(entry: &Projection, numbers: &[u64]) -> bool {
    let first = entry.turn - entry.span + 1;
    numbers
        .iter()
        .any(|number| (first..=entry.turn).contains(number))
}

/// Every row of a file through `parse`, numbered from 1.
fn rows<T>(
    file: &Path,
    conversation_id: &str,
    parse: impl Fn(&str, usize, &str) -> Result<T, crate::conversation::Refused>,
) -> Result<Vec<T>, Error> {
    let text = std::fs::read_to_string(file)?;
    text.lines()
        .enumerate()
        .map(|(index, line)| parse(line, index + 1, conversation_id).map_err(Error::from))
        .collect()
}

impl FileStore {
    /// Blank the selected turns of one conversation and every projection
    /// entry covering them; the count matched, already-redacted ones
    /// included, so the act is idempotent.
    pub fn redact_turns(
        &self,
        conversation_id: &str,
        through: Option<i64>,
        turns: Option<&[i64]>,
        actor: Option<&str>,
    ) -> Result<usize, Error> {
        let _held = self.lock()?;
        parse_conversation_id(conversation_id)?;
        let (through, named) = selection(through, turns)?;
        let created_at = Stamp::now();
        let directory = self.root.join(CONVERSATIONS).join(conversation_id);
        let turns_file = directory.join(TURNS);
        if !turns_file.is_file() {
            return Ok(0);
        }
        let mut recorded = rows(&turns_file, conversation_id, parse_turn)?;
        let matched: Vec<u64> = recorded
            .iter()
            .filter(|row| selected(row.turn, through, named.as_deref()))
            .map(|row| row.turn)
            .collect();
        if matched.is_empty() {
            return Ok(0);
        }
        for row in recorded
            .iter_mut()
            .filter(|row| matched.contains(&row.turn))
        {
            row.messages.clear();
            row.redacted = true;
        }
        let text: String = recorded.iter().map(render_turn).collect();
        atomic_write(&turns_file, &text, true)?;
        let projections_file = directory.join(PROJECTIONS);
        if projections_file.is_file() {
            let mut entries = rows(&projections_file, conversation_id, parse_projection)?;
            let mut blanked = false;
            for entry in entries.iter_mut().filter(|entry| covers(entry, &matched)) {
                blanked |= !entry.text.is_empty();
                entry.text.clear();
            }
            if blanked {
                let text: String = entries.iter().map(render_projection).collect();
                atomic_write(&projections_file, &text, true)?;
            }
        }
        let act = Redaction {
            conversation_id: conversation_id.to_string(),
            turns: matched,
            actor: actor.map(str::to_string),
            created_at,
        };
        append_line(&directory.join(REDACTIONS), &render_redaction(&act), true)?;
        Ok(act.turns.len())
    }

    /// The erasure trail, newest first, at most `limit`; `ids` narrows
    /// and an empty list answers nothing; `since` is inclusive.
    pub fn turn_redactions(
        &self,
        ids: Option<&[String]>,
        since: Option<Stamp>,
        limit: i64,
    ) -> Result<Vec<Redaction>, Error> {
        let _held = self.lock()?;
        if let Some(ids) = ids {
            for conversation_id in ids {
                parse_conversation_id(conversation_id)?;
            }
        }
        if limit < 1 {
            return Err(Error::Value(format!("limit must be >= 1, got {limit}")));
        }
        let parent = self.root.join(CONVERSATIONS);
        let held;
        let ids = match ids {
            Some([]) => return Ok(Vec::new()),
            Some(ids) => ids,
            None => {
                held = held_ids(&parent)?;
                &held
            }
        };
        let mut acts: Vec<(Redaction, usize)> = Vec::new();
        for conversation_id in ids {
            let trail = parent.join(conversation_id).join(REDACTIONS);
            if trail.is_file() {
                let read = rows(&trail, conversation_id, parse_redaction)?;
                acts.extend(read.into_iter().enumerate().map(|(at, act)| (act, at)));
            }
        }
        acts.retain(|(act, _)| {
            since.is_none_or(|since| act.created_at.instant() >= since.instant())
        });
        acts.sort_by(|(a, at_a), (b, at_b)| {
            (b.created_at.instant(), &b.conversation_id, at_b).cmp(&(
                a.created_at.instant(),
                &a.conversation_id,
                at_a,
            ))
        });
        acts.truncate(limit as usize);
        Ok(acts.into_iter().map(|(act, _)| act).collect())
    }
}
