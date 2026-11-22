//! `search_records` on a FileStore (DESIGN §40.54 item 11, T1b2a2). Pass
//! one streams each selected log with history search's raw-line prefilter:
//! a format-2 turn's records a line at a time, a whole format-1 turn
//! converted as the search reaches it (held as its legacy read holds it).
//! Pass two reads the winners back by turn, then names each tool result's
//! call in a second pass over its turn, keeping only the wanted call ids
//! (at most `limit`), never a map growing with a turn's calls. Drafts are
//! not in the log; a marked seal is rolled forward first.

use std::collections::BTreeMap;
use std::fs;
use std::io::BufReader;

use super::source::{Source, open_at};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rank::Order;
use crate::conversation::rows::{CONVERSATIONS, TURNS};
use crate::conversation::rows_header::Row;
use crate::conversation::search::{held_ids, read_at, scan_log};
use crate::conversation::turn_records::{
    Kind, RecordHit, RecordKey, RecordRanking, RecordSearch, TurnRecord, each_record,
    record_excerpt,
};
use crate::conversation::{Error, Refused};
use crate::limits;
use crate::store::FileStore;
use crate::store::time::Stamp;

/// A winner's turn: its conversation index, log offset and line, number.
type Place = (u32, u64, u32, u64);

/// A winner: its place in the answer, its key, its score.
type Wanted = (usize, RecordKey, Option<u64>);

/// The turns whose tool results wait for their calls' names, each waiting
/// hit with its call id: at most `limit` in all.
type Waiting = BTreeMap<Place, Vec<(usize, String)>>;

impl FileStore {
    /// Every hit of a record search, in answer order.
    pub fn search_records(&self, search: &RecordSearch) -> Result<Vec<RecordHit>, Error> {
        let maximum = match search.order {
            Order::Relevance => limits::search_candidates()?,
            Order::Newest => 0,
        };
        self.search_records_bounded(search, maximum)
    }

    /// `search_records` with the candidate bound given.
    pub(super) fn search_records_bounded(
        &self,
        search: &RecordSearch,
        maximum: usize,
    ) -> Result<Vec<RecordHit>, Error> {
        let _held = self.lock()?;
        let mut ids = match &search.conversations {
            Some(ids) => {
                for id in ids {
                    parse_conversation_id(id)?;
                }
                ids.clone()
            }
            None => held_ids(&self.root.join(CONVERSATIONS))?,
        };
        ids.sort_unstable();
        ids.dedup();
        if search.kinds.is_empty() {
            return Ok(Vec::new());
        }
        let records = self.records_enabled()?;
        let mut ranking = RecordRanking::new(search, maximum);
        for (index, id) in ids.iter().enumerate() {
            self.settle(id)?;
            let file = self.conversation_file(id, TURNS)?;
            if !file.is_file() {
                continue;
            }
            let conversation = u32::try_from(index).expect("conversation count fits u32");
            let all = search.order == Order::Newest;
            scan_log(
                &file,
                id,
                &search.terms,
                all,
                records,
                |row, place, line| {
                    let line = u32::try_from(line).unwrap_or(u32::MAX);
                    self.offer_turn(&mut ranking, id, row, (conversation, place, line))
                },
            )?;
        }
        self.hits(&ids, ranking.winners(), search, records)
    }

    /// Every record of one candidate row to the ranking, the turn skipped
    /// when out of the bounds or redacted.
    fn offer_turn(
        &self,
        ranking: &mut RecordRanking,
        id: &str,
        row: Row,
        (conversation, place, line): (u32, u64, u32),
    ) -> Result<(), Error> {
        let created = row.created_at().instant();
        if !ranking.admits(created) {
            return Ok(());
        }
        let turn = row.turn();
        let key = |seq: u64, offset: u64| RecordKey {
            created,
            conversation,
            turn,
            seq,
            place,
            line,
            offset,
        };
        match row {
            Row::Whole(whole) if whole.redacted => Ok(()),
            Row::Whole(whole) => {
                let mut refused = None;
                each_record(&whole.messages, &mut |record| {
                    if refused.is_none() {
                        refused = ranking.offer(key(record.seq, 0), &record).err();
                    }
                })
                .map_err(|why| unconvertible(id, turn, &why))?;
                refused.map_or(Ok(()), |why| Err(Error::Value(why)))
            }
            Row::Records(header) => {
                let path = self.record_file(id, header.turn)?;
                let mut source = open_at(&path, 0, 0, id, header.turn)?;
                while let Some((record, offset)) = source.next(id)? {
                    ranking
                        .offer(key(record.seq, offset), &record)
                        .map_err(Error::Value)?;
                }
                Ok(())
            }
        }
    }

    /// Pass two: the winners grouped by turn, each turn's row read once and
    /// a format-1 turn converted once, the hits kept in answer order; then
    /// each tool result's call named in a second pass over its turn.
    fn hits(
        &self,
        ids: &[String],
        winners: Vec<(RecordKey, Option<u64>)>,
        search: &RecordSearch,
        records: bool,
    ) -> Result<Vec<RecordHit>, Error> {
        let count = winners.len();
        let mut turns: BTreeMap<Place, Vec<Wanted>> = BTreeMap::new();
        for (at, (key, score)) in winners.into_iter().enumerate() {
            let place = (key.conversation, key.place, key.line, key.turn);
            turns.entry(place).or_default().push((at, key, score));
        }
        let mut slots: Vec<Option<RecordHit>> = (0..count).map(|_| None).collect();
        let mut waiting = Waiting::new();
        for (place, wanted) in turns {
            let id = &ids[place.0 as usize];
            let (row, actor) = self.row_at(id, place.1, place.2, records)?;
            self.winners_of(id, row, &wanted, &mut |(at, key, score), record| {
                if record.kind == Kind::ToolResult
                    && let Some(call_id) = record.call_id.clone()
                {
                    waiting.entry(place).or_default().push((*at, call_id));
                }
                slots[*at] = Some(RecordHit {
                    conversation_id: id.clone(),
                    turn: key.turn,
                    seq: record.seq,
                    message: record.message,
                    kind: record.kind,
                    name: record
                        .name
                        .clone()
                        .filter(|_| record.kind == Kind::ToolCall),
                    call_id: record.call_id,
                    created_at: Stamp::from_micros(key.created),
                    actor: actor.clone(),
                    size: record.size,
                    excerpt: record_excerpt(&record.text, &search.terms),
                    score: *score,
                });
            })?;
        }
        let mut hits = Vec::with_capacity(count);
        for slot in slots {
            hits.push(slot.expect("every winner's record was found or refused"));
        }
        for ((conversation, place, line, turn), wanted) in waiting {
            let id = &ids[conversation as usize];
            let (row, _) = self.row_at(id, place, line, records)?;
            self.each_turn_record(id, turn, row, &mut |record| {
                if record.kind != Kind::ToolCall {
                    return;
                }
                for (at, call_id) in &wanted {
                    if hits[*at].name.is_none() && record.call_id.as_ref() == Some(call_id) {
                        hits[*at].name = record.name.clone();
                    }
                }
            })?;
        }
        Ok(hits)
    }

    /// The wanted records of one turn's row, each to `visit`: a records file
    /// entered at each record's offset, or the whole turn converted once; a
    /// record not there refused by name.
    fn winners_of(
        &self,
        id: &str,
        row: Row,
        wanted: &[Wanted],
        visit: &mut dyn FnMut(&Wanted, TurnRecord),
    ) -> Result<(), Error> {
        let mut found = 0;
        let turn = row.turn();
        match row {
            Row::Whole(whole) => {
                each_record(&whole.messages, &mut |record| {
                    if let Some(winner) = wanted.iter().find(|w| w.1.seq == record.seq) {
                        found += 1;
                        visit(winner, record);
                    }
                })
                .map_err(|why| unconvertible(id, turn, &why))?;
            }
            Row::Records(header) => {
                let path = self.record_file(id, header.turn)?;
                for winner in wanted {
                    let key = &winner.1;
                    let mut source = open_at(&path, key.offset, key.seq, id, header.turn)?;
                    while let Some((record, _)) = source.next(id)? {
                        if record.seq == key.seq {
                            found += 1;
                            visit(winner, record);
                            break;
                        }
                    }
                }
            }
        }
        if found < wanted.len() {
            let why = format!("turn {turn}: a record the search found is gone");
            return Err(Refused::format_unsupported(id, &why).into());
        }
        Ok(())
    }

    /// The row whose log line starts at `place`, and its turn's actor.
    fn row_at(
        &self,
        id: &str,
        place: u64,
        line: u32,
        records: bool,
    ) -> Result<(Row, Option<String>), Error> {
        let file = fs::File::open(self.conversation_file(id, TURNS)?)?;
        let row = read_at(&mut BufReader::new(file), place, line as usize, id, records)?;
        let actor = match &row {
            Row::Whole(whole) => whole.actor.clone(),
            Row::Records(header) => header.actor.clone(),
        };
        Ok((row, actor))
    }

    /// Every record of one turn, in order, to `visit`.
    fn each_turn_record(
        &self,
        id: &str,
        turn: u64,
        row: Row,
        visit: &mut dyn FnMut(TurnRecord),
    ) -> Result<(), Error> {
        match row {
            Row::Whole(whole) => {
                each_record(&whole.messages, visit).map_err(|why| unconvertible(id, turn, &why))
            }
            Row::Records(header) => {
                let path = self.record_file(id, header.turn)?;
                let mut source: Source = open_at(&path, 0, 0, id, header.turn)?;
                while let Some((record, _)) = source.next(id)? {
                    visit(record);
                }
                Ok(())
            }
        }
    }
}

/// A whole turn the codec cannot convert (an id or a name past 1,024
/// bytes), refused as the record read refuses it.
fn unconvertible(id: &str, turn: u64, why: &str) -> Error {
    Refused::format_unsupported(id, &format!("turn {turn}: {why}")).into()
}
