//! What `prune` reads of one conversation (finding 7 of the T1 review): when
//! its newest turn was written and whether every turn is erased. A store
//! that keeps records answers both from its turn headers, read a page at a
//! time, so a turn longer than `NEOSIAN_MAX_TURN_BYTES` is never assembled
//! and no longer stops the plan; a store without records answers from its
//! turns, as `prune` always read them.

use crate::conversation::Error;
use crate::conversation::turn_records::{Header, RecordRead};
use crate::state::State;
use crate::state::records::{Fault, Records};
use crate::store::time::Stamp;

/// One conversation of the store a prune plans, and the store's records
/// while it keeps them.
pub(super) struct Ages<'a> {
    pub store: &'a dyn State,
    pub records: Option<&'a dyn Records>,
    pub id: &'a str,
}

impl Ages<'_> {
    /// Each header of turn `turn`, or of every turn, handed to `visit`.
    fn headers(
        &self,
        records: &dyn Records,
        turn: Option<u64>,
        visit: &mut dyn FnMut(Header),
    ) -> Result<(), Error> {
        let read = RecordRead {
            turn,
            kinds: Some(Vec::new()),
            ..RecordRead::default()
        };
        let mut page = |page: crate::remote::records::RecordPage| {
            page.turns.into_iter().for_each(&mut *visit);
            Ok(true)
        };
        records
            .read(self.id, &read, &mut page)
            .map_err(Fault::into_error)
    }

    /// When turn `last`, the newest, was written.
    pub fn newest(&self, last: u64) -> Result<Stamp, Error> {
        let mut newest = None;
        match self.records {
            Some(records) => {
                self.headers(records, Some(last), &mut |header| {
                    newest = Some(header.created_at);
                })?;
            }
            None => {
                let turns = self.store.read_turns(self.id, (last - 1) as i64, Some(1))?;
                newest = turns.first().map(|turn| turn.created_at);
            }
        }
        newest.ok_or_else(|| Error::Value("the newest turn is missing".to_string()))
    }

    /// Whether every turn of the conversation is erased.
    pub fn all_erased(&self) -> Result<bool, Error> {
        let Some(records) = self.records else {
            let turns = self.store.read_turns(self.id, 0, None)?;
            return Ok(turns.iter().all(|turn| turn.redacted));
        };
        let mut erased = true;
        self.headers(records, None, &mut |header| erased &= header.redacted)?;
        Ok(erased)
    }
}
