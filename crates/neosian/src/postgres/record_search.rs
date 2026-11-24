//! Search the virtual records of legacy turns and the mapped physical
//! records in one snapshot. Both sources compete for one set of winners.
mod legacy;
mod native;
#[cfg(test)]
mod tests;
mod winners;

pub(super) use native::predicate;

use std::cmp::Ordering;
use std::collections::HashSet;
use std::rc::Rc;

use tokio_postgres::Transaction;

use super::{PostgresStore, record_read};
use crate::conversation::Error;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{RecordHit, RecordRanking, RecordSearch};
use crate::limits;
use crate::store::error::MemoryError;

pub(super) struct Context<'a, 'c> {
    pub(super) tx: &'a Transaction<'c>,
    pub(super) statements: &'a record_read::Statements,
    pub(super) schema: &'a str,
    pub(super) search: &'a RecordSearch,
    pub(super) budget: u64,
}

struct Location {
    id: Rc<str>,
    created: i64,
    turn: u64,
    seq: u64,
    legacy: bool,
}

fn newest(a: &Location, b: &Location) -> Ordering {
    b.created
        .cmp(&a.created)
        .then_with(|| b.id.cmp(&a.id))
        .then_with(|| b.turn.cmp(&a.turn))
        .then_with(|| a.seq.cmp(&b.seq))
}

pub(super) struct Candidates {
    ranking: RecordRanking<Location>,
    // Only relevance holds all matching keys. Newest drops a losing key
    // and its id at once even when a search crosses the entire database.
    ids: Option<HashSet<Rc<str>>>,
}

impl Candidates {
    fn winners(self) -> Vec<(Location, Option<u64>)> {
        self.ranking.winners()
    }

    pub(super) fn offer(
        &mut self,
        id: &str,
        created: i64,
        turn: u64,
        seq: u64,
        legacy: bool,
        facts: Option<(Vec<u32>, u64)>,
    ) -> Result<(), Error> {
        let id = match &mut self.ids {
            Some(ids) => match ids.get(id) {
                Some(name) => name.clone(),
                None => {
                    let name = Rc::<str>::from(id);
                    ids.insert(name.clone());
                    name
                }
            },
            None => Rc::from(id),
        };
        let key = Location {
            id,
            created,
            turn,
            seq,
            legacy,
        };
        self.ranking
            .offer_selected(
                key,
                facts
                    .as_ref()
                    .map(|(counts, length)| (counts.as_slice(), *length)),
            )
            .map_err(Error::Value)
    }
}

impl PostgresStore {
    pub fn search_records(&self, search: &RecordSearch) -> Result<Vec<RecordHit>, Error> {
        let maximum = match search.order {
            Order::Relevance => limits::search_candidates()?,
            Order::Newest => 0,
        };
        self.search_records_bounded(search, maximum)
    }

    pub(super) fn search_records_bounded(
        &self,
        search: &RecordSearch,
        maximum: usize,
    ) -> Result<Vec<RecordHit>, Error> {
        if let Some(ids) = &search.conversations {
            for id in ids {
                parse_conversation_id(id)?;
            }
        }
        if !self.supports_records()? {
            return Err(MemoryError::configuration(
                "records on Postgres require operator-installed records extension 1",
            )
            .into());
        }
        if search.kinds.is_empty() || search.conversations.as_ref().is_some_and(Vec::is_empty) {
            return Ok(Vec::new());
        }
        let budget = limits::response_bytes()?;
        self.pool.record_snapshot(async |tx, statements| {
            let cx = Context {
                tx,
                statements,
                schema: self.pool.schema(),
                search,
                budget,
            };
            let mut candidates = Candidates {
                ranking: RecordRanking::with_order(search, maximum, newest),
                ids: (search.order == Order::Relevance).then(HashSet::new),
            };
            native::offer(&cx, &mut candidates).await?;
            legacy::offer(&cx, &mut candidates).await?;
            winners::hits(&cx, candidates.winners()).await
        })
    }
}
