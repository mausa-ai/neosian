//! History relevance on Postgres in two passes (DESIGN §40.45): the facts
//! statement streams every candidate row and keeps only its facts, counting
//! over the stored `search_text`; the winners' turns are then read whole, at
//! most `limit` of them, in the same snapshot and under the response budget.

use super::connection::Passes;
use super::{PostgresStore, rows, sql};
use crate::conversation::rank::{Key, Ranked, Ranking, Request, Winner};
use crate::conversation::{Error, Refused, Turn};
use crate::remote::codec;
use crate::store::error::Error as StoreError;
use serde_json::{Value, json};
use std::collections::HashMap;

impl PostgresStore {
    pub(super) fn rank_relevance(
        &self,
        request: &Request,
        params: &Value,
    ) -> Result<Vec<Ranked>, Error> {
        let mut passes = Winners {
            ranking: Some(Ranking::new(request, true)?),
            winners: Vec::new(),
            found: HashMap::new(),
            refused: None,
            budget: crate::limits::response_bytes()?,
            used: 0,
        };
        let outcome = self
            .pool
            .snapshot(sql!("store/rank_turn_facts"), params, &mut passes);
        if let Some(refused) = passes.refused.take() {
            return Err(refused);
        }
        outcome?;
        passes
            .winners
            .into_iter()
            .map(|(key, _, score)| {
                let turn = passes.found.remove(&(key.conversation, key.turn));
                let turn = turn.ok_or_else(|| std::io::Error::other("a ranked turn vanished"))?;
                Ok((turn, score))
            })
            .collect()
    }
}

/// Relevance's two passes: the facts rows feed the ranking; the winners' rows
/// are decoded whole, at most `limit` of them, under the response budget. A
/// conversation refusal stops the read and is reported as itself.
struct Winners {
    ranking: Option<Ranking>,
    winners: Vec<Winner>,
    found: HashMap<(u32, u64), Turn>,
    refused: Option<Error>,
    budget: u64,
    used: u64,
}

impl Winners {
    fn stop(&mut self, error: Error) -> StoreError {
        let note = StoreError::Value(String::new());
        self.refused = Some(error);
        note
    }
}

impl Passes for Winners {
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
        self.winners = self.ranking.take().expect("one ranking").winners();
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

    fn then(&mut self, row: Value) -> Result<(), StoreError> {
        self.used = self.used.saturating_add(super::values::weight(&row));
        crate::limits::check(crate::limits::RESPONSE, self.used, self.budget)?;
        let decoded = match codec::text(&row, "conversation_id") {
            Ok(id) => rows::turn(&id, row),
            Err(error) => Err(error.into()),
        };
        let turn = decoded.map_err(|error| self.stop(error))?;
        let conversation = self
            .winners
            .iter()
            .find(|(key, id, _)| *id == turn.conversation_id && key.turn == turn.turn)
            .map(|(key, _, _)| key.conversation);
        if let Some(conversation) = conversation {
            self.found.insert((conversation, turn.turn), turn);
        }
        Ok(())
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
