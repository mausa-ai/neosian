//! History relevance on Postgres in two passes (DESIGN §40.45): the facts
//! statement streams every candidate row and keeps only its facts, counting
//! over the stored `search_text`; the winners' turns are then read in rank
//! order in the same snapshot, under the response budget, and handed one at a
//! time to the visitor, which may stop the page (§40.46).

use super::connection::Passes;
use super::{PostgresStore, rows, sql};
use crate::conversation::page::{Mode, Resume, Visit, owed};
use crate::conversation::rank::{Key, Ranking, Request, Winner};
use crate::conversation::{Error, Refused};
use crate::remote::codec;
use crate::store::error::Error as StoreError;
use serde_json::{Value, json};

impl PostgresStore {
    pub(super) fn rank_relevance(
        &self,
        request: &Request,
        params: &Value,
        resume: &Resume,
        mode: Mode,
        visit: &mut Visit,
    ) -> Result<Option<String>, Error> {
        let mut passes = Winners {
            mode,
            ranking: Some(Ranking::new(request, true)?),
            resume,
            visit,
            winners: Vec::new(),
            digest: String::new(),
            read: 0,
            stopped: false,
            refused: None,
            budget: crate::limits::response_bytes()?,
            used: 0,
        };
        let outcome = self
            .pool
            .snapshot(sql!("store/rank_turn_facts"), params, mode, &mut passes);
        if let Some(refused) = passes.refused.take() {
            return Err(refused);
        }
        outcome?;
        if !passes.stopped && passes.read != passes.winners.len() {
            return Err(std::io::Error::other("a ranked turn vanished").into());
        }
        Ok(Some(passes.digest))
    }
}

/// Relevance's two passes: the facts rows feed the ranking; the winners the
/// page still owes are read in rank order under the response budget, each
/// row alone unless the caller collects them (§40.46, #417). A
/// conversation refusal, or one from the visitor, stops the read and is
/// reported as itself.
struct Winners<'a, 'b> {
    mode: Mode,
    ranking: Option<Ranking>,
    resume: &'a Resume,
    visit: &'a mut Visit<'b>,
    winners: Vec<Winner>,
    digest: String,
    read: usize,
    stopped: bool,
    refused: Option<Error>,
    budget: u64,
    used: u64,
}

impl Winners<'_, '_> {
    fn stop(&mut self, error: Error) -> StoreError {
        let note = StoreError::Value(String::new());
        self.refused = Some(error);
        note
    }
}

impl Passes for Winners<'_, '_> {
    fn first(&mut self, row: Value) -> Result<bool, StoreError> {
        let ranking = self.ranking.as_mut().expect("ranking before winners");
        let fact = (|| {
            let id = codec::text(&row, "conversation_id")?;
            let number = codec::number(&row, "turn")?;
            turn_format(&id, number, &row)?;
            let created = codec::moment(&row, "created_at")?.instant();
            let key = Key {
                created,
                conversation: ranking.conversation(&id),
                turn: number,
                place: 0,
                line: 0,
            };
            ranking.offer(key, row["search_text"].as_str().unwrap_or_default())
        })();
        match fact {
            Ok(()) => Ok(true),
            Err(error) => Err(self.stop(error)),
        }
    }

    fn second(&mut self) -> Result<Option<(&'static str, Value)>, StoreError> {
        let ranking = self.ranking.take().expect("one ranking");
        self.digest = ranking.digest();
        self.winners = match owed(ranking.winners(), self.resume, &self.digest) {
            Ok(winners) => winners,
            Err(error) => return Err(self.stop(error)),
        };
        if self.winners.is_empty() {
            return Ok(None);
        }
        let ids: Vec<&str> = self.winners.iter().map(|(_, id, _)| id.as_str()).collect();
        let turns: Vec<u64> = self.winners.iter().map(|(key, _, _)| key.turn).collect();
        Ok(Some((
            sql!("store/read_ranked_turns"),
            json!({"ids": ids, "turns": turns}),
        )))
    }

    fn then(&mut self, row: Value) -> Result<bool, StoreError> {
        self.used = self.mode.charge(self.used, super::values::weight(&row));
        crate::limits::check(crate::limits::RESPONSE, self.used, self.budget)?;
        let decoded = match codec::text(&row, "conversation_id") {
            Ok(id) => rows::turn(&id, row),
            Err(error) => Err(error.into()),
        };
        let turn = decoded.map_err(|error| self.stop(error))?;
        let Some((key, id, score)) = self.winners.get(self.read) else {
            return Err(std::io::Error::other("a ranked turn vanished").into());
        };
        if *id != turn.conversation_id || key.turn != turn.turn {
            return Err(std::io::Error::other("a ranked turn vanished").into());
        }
        let score = *score;
        self.read += 1;
        match (self.visit)(turn, score) {
            Ok(go) => {
                self.stopped = !go;
                Ok(go)
            }
            Err(error) => Err(self.stop(error)),
        }
    }
}

/// The format guard `rows::turn` applies, for a facts row that carries no messages.
fn turn_format(id: &str, number: u64, row: &Value) -> Result<(), Error> {
    let declared = row["neosian_format"].as_i64().unwrap_or(0);
    if !(1..=crate::conversation::FORMAT as i64).contains(&declared) {
        return Err(Refused::format_unsupported(
            id,
            &format!("turn row {number} declares unsupported format {declared}"),
        )
        .into());
    }
    Ok(())
}
