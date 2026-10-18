//! The wire's pages (`server/paging.py`, DESIGN §18.2): no listing answers
//! more than `PAGE` rows, and each names what to send back for the rest.
//! The memory listings page through the store's cursors and answer
//! `next_cursor`; the conversation reads carry their own cursor and answer
//! `next_after`, a cut projection page ending on a whole turn.

use super::envelope::Fail;
use super::params::{Payload, optional_int, optional_str, require_str};
use crate::conversation::{Projection, Turn};
use crate::store::{FileStore, Page};

/// The most rows one response carries.
pub const PAGE: i64 = 500;

/// `memory_page`: one bounded page of a memory listing. `whole` receives
/// the caller's limit (zero: the read for its checks, and nothing).
pub fn memory_page<T>(
    payload: &Payload,
    default_limit: Option<i64>,
    whole: impl FnOnce(i64) -> Result<Vec<T>, Fail>,
    page: impl FnOnce(Option<&str>, i64) -> Result<Page<T>, Fail>,
) -> Result<Page<T>, Fail> {
    let cursor = optional_str(payload, "cursor")?;
    let limit = optional_int(payload, "limit")?.or(default_limit);
    if limit.is_some_and(|limit| limit < 0) {
        return Err(Fail::value("limit must be >= 0"));
    }
    if limit == Some(0) {
        whole(0)?;
        return Ok(Page {
            items: Vec::new(),
            next_cursor: None,
        });
    }
    page(
        cursor.as_deref(),
        limit.map_or(PAGE, |limit| limit.min(PAGE)),
    )
}

fn window(payload: &Payload) -> Result<(String, i64, Option<i64>, i64), Fail> {
    let conversation_id = require_str(payload, "conversation_id")?;
    let after = optional_int(payload, "after")?.unwrap_or(0);
    let limit = optional_int(payload, "limit")?;
    let size = limit.map_or(PAGE, |limit| limit.min(PAGE));
    Ok((conversation_id, after, limit, size))
}

/// `turns_page`: the turns past `after`, and where the next page starts.
pub fn turns_page(store: &FileStore, payload: &Payload) -> Result<(Vec<Turn>, Option<u64>), Fail> {
    let (conversation_id, after, limit, size) = window(payload)?;
    let turns = store.read_turns(&conversation_id, after, Some(size))?;
    if (turns.len() as i64) < size || Some(size) == limit {
        return Ok((turns, None));
    }
    let next = turns.last().map(|turn| turn.turn);
    Ok((turns, next))
}

/// `projections_page`: as `turns_page`, but a cut page ends on a whole
/// turn, widening rather than advancing when one turn fills it.
pub fn projections_page(
    store: &FileStore,
    payload: &Payload,
) -> Result<(Vec<Projection>, Option<u64>), Fail> {
    let (conversation_id, after, limit, mut size) = window(payload)?;
    loop {
        let entries = store.read_projections(&conversation_id, after, Some(size))?;
        if (entries.len() as i64) < size || Some(size) == limit {
            return Ok((entries, None));
        }
        let boundary = entries.last().map_or(0, |entry| entry.turn);
        let complete: Vec<Projection> = entries
            .into_iter()
            .filter(|entry| entry.turn < boundary)
            .collect();
        if !complete.is_empty() {
            return Ok((complete, Some(boundary - 1)));
        }
        size = limit.map_or(size * 2, |limit| (size * 2).min(limit));
    }
}
