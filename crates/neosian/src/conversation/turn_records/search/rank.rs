//! The two passes of a record search (§40.54 item 11) with the ranking's
//! generic parts (`Facts`, `Stats`, `Best`) and a record key. Pass one
//! offers each record a store reaches and keeps a key, and under relevance
//! its counts and length, never its text; `winners` orders newest (records
//! holding every term: turn time, conversation and turn descending, `seq`
//! ascending) or scores relevance (records holding a term, memory ranking
//! version 1 over the candidates, ties in newest order). Memory follows the
//! candidates and `limit`, never a record's or a turn's size.

use super::super::TurnRecord;
use super::{RecordSearch, record_facts};
use crate::conversation::rank::{Order, within};
use crate::memory::search::rank::{Best, Facts, Stats};

/// Where a candidate record is: its turn's time, its conversation as an
/// index into the store's sorted list (so indexes order as ids do), its
/// turn, its `seq`, and on files the turn's log line and the record's
/// offset in its records file (0 for a whole turn converted).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RecordKey {
    pub created: i64,
    pub conversation: u32,
    pub turn: u64,
    pub seq: u64,
    pub place: u64,
    pub line: u32,
    pub offset: u64,
}

/// Item 11's newest order.
fn newest(a: &RecordKey, b: &RecordKey) -> std::cmp::Ordering {
    b.created
        .cmp(&a.created)
        .then_with(|| b.conversation.cmp(&a.conversation))
        .then_with(|| b.turn.cmp(&a.turn))
        .then_with(|| a.seq.cmp(&b.seq))
}

/// Pass one of a record search and its winners.
pub struct RecordRanking {
    order: Order,
    limit: usize,
    search: RecordSearch,
    stats: Stats,
    facts: Facts<RecordKey>,
    newest: Best<RecordKey>,
}

impl RecordRanking {
    /// `maximum` is the candidate bound, counted in records, under
    /// relevance (`NEOSIAN_SEARCH_MAX_CANDIDATES`).
    pub fn new(search: &RecordSearch, maximum: usize) -> Self {
        let terms = search.terms.len();
        Self {
            order: search.order,
            limit: search.limit,
            search: search.clone(),
            stats: Stats::new(terms),
            facts: Facts::new(terms, maximum),
            newest: Best::new(search.limit),
        }
    }

    /// Whether a turn made at `created` lies inside the bounds, so that a
    /// store reads its records at all.
    pub fn admits(&self, created: i64) -> bool {
        within(self.search.bounds, created)
    }

    /// One record of an admitted turn; refused past the candidate bound.
    pub fn offer(&mut self, key: RecordKey, record: &TurnRecord) -> Result<(), String> {
        if !self.search.kinds.contains(&record.kind) || !self.admits(key.created) {
            return Ok(());
        }
        let (counts, length) = record_facts(record, &self.search.terms);
        match self.order {
            Order::Newest if counts.iter().all(|count| *count > 0) => {
                self.newest.offer(key, newest);
            }
            Order::Relevance if counts.iter().any(|count| *count > 0) => {
                self.stats.add(&counts, length);
                self.facts.keep(key, &counts, length).map_err(|broad| {
                    crate::limits::broad(
                        broad.0,
                        "records",
                        "the words, the kinds or the time range",
                    )
                })?;
            }
            _ => {}
        }
        Ok(())
    }

    /// The winners in answer order, each with its score under relevance.
    pub fn winners(self) -> Vec<(RecordKey, Option<u64>)> {
        match self.order {
            Order::Newest => self
                .newest
                .into_vec(newest)
                .into_iter()
                .map(|key| (key, None))
                .collect(),
            Order::Relevance => self
                .facts
                .best(&self.stats, self.limit, newest)
                .into_iter()
                .map(|(key, score)| (key, Some(score)))
                .collect(),
        }
    }
}
