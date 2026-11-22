//! History search over a FileStore (`conversation/file_search.py`, DESIGN
//! §32, §40.43, §40.45): every selected log streamed once for facts, the
//! winners read again by offset under the same lock. Where records are
//! enabled (§40.54 item 15.1) each conversation's marked seal is rolled
//! forward first; a turn stored as records within `NEOSIAN_MAX_TURN_BYTES`
//! is matched and answered as the same messages stored whole; a longer one
//! is counted from its records a record at a time, keeps its place among
//! the winners, and is handed on as omitted instead of whole.

use std::fs;

use super::FileStore;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::page::{Hit, HitVisit, Last, Mode, Resume, Visit, owed};
use crate::conversation::rank::{Key, Order, Ranked, Ranking, Request};
use crate::conversation::rows::{CONVERSATIONS, TURNS};
use crate::conversation::rows_header::Row;
use crate::conversation::search::{held_ids, read_at, scan_log, turn_text};
use crate::conversation::{Error, Turn};
use crate::limits;

impl FileStore {
    /// The turns holding every term, newest first, at most `limit`;
    /// `ids` narrows to those conversations, and an empty list answers
    /// nothing.
    pub fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, Error> {
        let mut found = Vec::new();
        self.search_each(terms, ids, limit, &mut |turn| {
            found.push(turn);
            Ok(true)
        })?;
        Ok(found)
    }

    /// `search_turns` one turn at a time, newest first (DESIGN §40.46).
    pub fn search_each(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let _held = self.lock()?;
        if let Some(ids) = ids {
            for conversation_id in ids {
                parse_conversation_id(conversation_id)?;
            }
            if ids.is_empty() {
                return Ok(());
            }
        }
        let listing = match ids {
            Some(ids) => Listing::Given(ids),
            None => Listing::All(held_ids(&self.root.join(CONVERSATIONS))?),
        };
        // Version 1 is newest without bounds: the same pipeline, at most
        // `limit` keys held while the logs stream (DESIGN §40.45).
        let request = Request {
            terms: terms.to_vec(),
            ids: None,
            limit,
            order: Order::Newest,
            since: None,
            before: None,
        };
        let mut turns = |hit: Hit, _| match hit {
            Hit::Turn(turn) => visit(turn),
            Hit::Omitted(_) => Ok(true),
        };
        self.rank_logs(&request, listing, &Resume::Fresh, &mut turns, None)?;
        Ok(())
    }

    /// History ranking version 1 under the store lock (DESIGN §40.43).
    pub fn rank_turns(&self, request: &Request) -> Result<Vec<Ranked>, Error> {
        let mut ranked = Vec::new();
        self.rank_each(request, &Resume::Fresh, Mode::Each, &mut |turn, score| {
            ranked.push((turn, score));
            Ok(true)
        })?;
        Ok(ranked)
    }

    /// Ranked or newest search handing each answerable turn to `visit` in
    /// answer order, from `resume` (DESIGN §40.46); under relevance it
    /// answers the candidate statistics' digest. Files keep no response
    /// budget, so the read mode changes nothing here. A turn too long to
    /// answer whole is passed over; the route's `rank_hits` names it.
    pub fn rank_each(
        &self,
        request: &Request,
        resume: &Resume,
        mode: Mode,
        visit: &mut Visit,
    ) -> Result<Option<String>, Error> {
        self.rank_hits(request, resume, mode, &mut |hit, score| match hit {
            Hit::Turn(turn) => visit(turn, score),
            Hit::Omitted(_) => Ok(true),
        })
    }

    /// The route's search: every winner in answer order, a turn too long to
    /// answer whole as `Hit::Omitted` in its place.
    pub fn rank_hits(
        &self,
        request: &Request,
        resume: &Resume,
        _mode: Mode,
        visit: &mut HitVisit,
    ) -> Result<Option<String>, Error> {
        self.rank_bounded(request, resume, visit, None)
    }

    /// `rank_hits` with the turn bound given, not read from the environment.
    pub(super) fn rank_bounded(
        &self,
        request: &Request,
        resume: &Resume,
        visit: &mut HitVisit,
        max_turn_bytes: Option<u64>,
    ) -> Result<Option<String>, Error> {
        request.bounds().map_err(Error::Value)?;
        let _held = self.lock()?;
        let listing = match &request.ids {
            Some(ids) => {
                for conversation_id in ids {
                    parse_conversation_id(conversation_id)?;
                }
                Listing::Given(ids)
            }
            None => Listing::All(held_ids(&self.root.join(CONVERSATIONS))?),
        };
        self.rank_logs(request, listing, resume, visit, max_turn_bytes)
    }

    /// Pass one streams every selected log and keeps facts; pass two reads
    /// the winners' lines again by offset and hands each to `visit`. The
    /// caller holds the lock, so the logs cannot change between the passes.
    fn rank_logs(
        &self,
        request: &Request,
        listing: Listing,
        resume: &Resume,
        visit: &mut HitVisit,
        max_turn_bytes: Option<u64>,
    ) -> Result<Option<String>, Error> {
        let records = self.records_enabled()?;
        let max = match max_turn_bytes {
            Some(max) => max,
            None if records => limits::Records::load()?.max_turn_bytes,
            None => 0,
        };
        let mut ranking = Ranking::new(request, false)?;
        if let Resume::After(last) = resume {
            ranking.resume_after(last.clone());
        }
        let all = request.order == Order::Newest;
        let scan = |ranking: &mut Ranking, id: &str, conversation: u32| {
            if records {
                self.settle(id)?;
            }
            let file = self.conversation_file(id, TURNS)?;
            if !file.is_file() {
                return Ok::<(), Error>(());
            }
            scan_log(
                &file,
                id,
                &request.terms,
                all,
                records,
                |row, place, line| {
                    let key = Key {
                        created: row.created_at().instant(),
                        conversation,
                        turn: row.turn(),
                        place,
                        line: u32::try_from(line).unwrap_or(u32::MAX),
                    };
                    self.offer_row(ranking, key, id, row, max)
                },
            )
        };
        match listing {
            Listing::Given(ids) => {
                for id in ids {
                    let conversation = ranking.conversation(id);
                    scan(&mut ranking, id, conversation)?;
                }
            }
            Listing::All(mut ids) => {
                // Sorted, the listing is the id table: no second copy (#417).
                ids.sort_unstable();
                ids.dedup();
                let count = u32::try_from(ids.len()).expect("conversation count fits u32");
                ranking.adopt_sorted(ids);
                for conversation in 0..count {
                    let id = ranking.name(conversation).to_string();
                    scan(&mut ranking, &id, conversation)?;
                }
            }
        }
        let digest = (request.order == Order::Relevance).then(|| ranking.digest());
        let winners = owed(ranking.winners(), resume, digest.as_deref().unwrap_or(""))?;
        // One log open at a time, whatever the number of conversations: the
        // previous one closes before the next opens.
        let mut open: Option<(u32, std::io::BufReader<fs::File>)> = None;
        for (key, id, score) in winners {
            if open
                .as_ref()
                .is_none_or(|(held, _)| *held != key.conversation)
            {
                drop(open.take());
                let file = fs::File::open(self.conversation_file(&id, TURNS)?)?;
                open = Some((key.conversation, std::io::BufReader::new(file)));
            }
            let (_, reader) = open.as_mut().expect("a log is open");
            let row = read_at(reader, key.place, key.line as usize, &id, records)?;
            let hit = match row {
                Row::Records(header) if header.bytes > max => Hit::Omitted(Last {
                    created: key.created,
                    conversation: id,
                    turn: header.turn,
                }),
                row => Hit::Turn(self.whole(&id, row, max)?),
            };
            if !visit(hit, score)? {
                break;
            }
        }
        Ok(digest)
    }

    /// One candidate row's facts: a whole turn's text, a records turn within
    /// the bound assembled, a longer one counted from its records alone.
    fn offer_row(
        &self,
        ranking: &mut Ranking,
        key: Key,
        id: &str,
        row: Row,
        max: u64,
    ) -> Result<(), Error> {
        match row {
            Row::Whole(turn) => ranking.offer(key, &turn_text(&turn)),
            Row::Records(header) if header.bytes <= max => {
                let turn = self.whole(id, Row::Records(header), max)?;
                ranking.offer(key, &turn_text(&turn))
            }
            Row::Records(header) if ranking.admits(key.created) => {
                let (counts, length) = self.record_facts(id, header.turn, ranking.terms())?;
                ranking.offer_facts(key, &counts, || length)
            }
            Row::Records(_) => Ok(()),
        }
    }
}

/// The conversations a file search reads: those a request names, in its
/// order, or every one the store holds.
enum Listing<'a> {
    Given(&'a [String]),
    All(Vec<String>),
}
