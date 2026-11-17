//! The turn ABC, search, projections and erasure over atomic SQL statements.

use super::{PostgresStore, rows, sql};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::page::{Last, Mode, Resume, Visit};
use crate::conversation::rank::{Order, Ranked, Request};
use crate::conversation::search::messages_text;
use crate::conversation::{
    Error, FORMAT, Projection, Redaction, Refused, Turn,
    message::{self, Message},
};
use crate::remote::codec;
use crate::store::time::Stamp;
use crate::wire::encode;
use serde_json::{Value, json};

/// The rows a turn-read page asks for first, before it knows their size.
const FIRST: i64 = 2;

impl PostgresStore {
    pub fn append_turn(
        &self,
        id: &str,
        messages: Vec<Message>,
        actor: Option<String>,
    ) -> Result<Turn, Error> {
        parse_conversation_id(id)?;
        if messages.is_empty() {
            return Err(Error::Value(
                "a turn must carry at least one message".into(),
            ));
        }
        let created_at = Stamp::now();
        let encoded: Vec<Value> = messages.iter().map(message::to_json).collect();
        let row = self.one(
            sql!("store/append_turn"),
            json!({
                "conversation_id":id,"messages":encoded,"now":created_at.z(),"format":FORMAT,
                "actor":actor,"search_text":messages_text(&messages)
            }),
            true,
        )?;
        Ok(Turn {
            conversation_id: id.into(),
            turn: codec::number(&row, "turn")?,
            messages,
            created_at,
            actor,
            redacted: false,
        })
    }

    pub fn read_turns(&self, id: &str, after: i64, limit: Option<i64>) -> Result<Vec<Turn>, Error> {
        let mut turns = Vec::new();
        self.read_turns_in(id, after, limit, Mode::Each, &mut |turn| {
            turns.push(turn);
            Ok(true)
        })?;
        Ok(turns)
    }

    /// `read_turns` one turn at a time (DESIGN §40.46).
    pub fn read_turns_each(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        self.read_turns_in(id, after, limit, Mode::Each, visit)
    }

    /// `read_turns` one turn at a time, read and charged as `mode` says.
    pub fn read_turns_in(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        mode: Mode,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        parse_conversation_id(id)?;
        rows::window(after, limit)?;
        let Mode::Paged(ceiling) = mode else {
            let params = json!({"conversation_id":id,"after":after,"limit":limit});
            return self.turn_rows(sql!("store/read_turns"), &params, mode, |row| {
                visit(rows::turn(id, row)?)
            });
        };
        self.read_turns_page(id, after, limit, ceiling, visit)
    }

    /// One size page of turns: short keyset statements by turn number, each
    /// asking for the rows the page still has room for, estimated from the
    /// compact JSON size of the rows read so far, plus two (one to say whether
    /// more follow, one for the estimate), so a page reads about two rows
    /// more than it keeps, in one round trip per statement and no
    /// transaction (DESIGN §40.46, #417). Every row is charged alone.
    fn read_turns_page(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        ceiling: u64,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let (mut last, mut taken, mut weight) = (after, 0_i64, 0_u64);
        let mut ask = FIRST.min(limit.unwrap_or(i64::MAX));
        loop {
            let params = json!({"conversation_id": id, "after": last, "limit": ask});
            let (mut read, mut open) = (0_i64, true);
            self.turn_rows(sql!("store/read_turns"), &params, Mode::Each, |row| {
                read += 1;
                weight = weight.saturating_add(super::values::encoded(&row));
                let turn = rows::turn(id, row)?;
                last = turn.turn as i64;
                open = visit(turn)?;
                Ok(open)
            })?;
            taken += read;
            let left = limit.map_or(i64::MAX, |limit| limit - taken);
            if !open || read < ask || left <= 0 {
                return Ok(());
            }
            let average = (weight / taken as u64).max(1);
            let room = ceiling.saturating_sub(weight) / average + 1;
            ask = i64::try_from(room).unwrap_or(i64::MAX).clamp(1, left);
        }
    }

    /// Streamed rows with conversation refusals reported as themselves.
    pub(super) fn turn_rows(
        &self,
        template: &'static str,
        params: &Value,
        mode: Mode,
        mut each: impl FnMut(Value) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let mut refused = None;
        let outcome = self.pool.rows(template, params, mode, |row| {
            each(row).map_err(|error| {
                refused = Some(error);
                crate::store::error::Error::Value(String::new())
            })
        });
        if let Some(error) = refused {
            return Err(error);
        }
        Ok(outcome?)
    }

    pub fn last_turn_number(&self, id: &str) -> Result<u64, Error> {
        parse_conversation_id(id)?;
        Ok(codec::number(
            &self.one(
                sql!("store/last_turn_number"),
                json!({"conversation_id":id}),
                false,
            )?,
            "coalesce",
        )?)
    }

    pub fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, Error> {
        let mut found = Vec::new();
        self.search_in(terms, ids, limit, Mode::Each, &mut |turn| {
            found.push(turn);
            Ok(true)
        })?;
        Ok(found)
    }

    pub fn search_each(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        self.search_in(terms, ids, limit, Mode::Each, visit)
    }

    /// Version 1 search: newest without bounds, the same statement.
    fn search_in(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        mode: Mode,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        check_ids(ids)?;
        if limit < 1 {
            return Err(Error::Value(format!("limit must be >= 1, got {limit}")));
        }
        let request = Request {
            terms: terms.to_vec(),
            ids: ids.map(<[String]>::to_vec),
            limit,
            order: Order::Newest,
            since: None,
            before: None,
        };
        self.rank_each(&request, &Resume::Fresh, mode, &mut |turn, _| visit(turn))?;
        Ok(())
    }

    pub fn rank_turns(&self, request: &Request) -> Result<Vec<Ranked>, Error> {
        let mut ranked = Vec::new();
        self.rank_each(request, &Resume::Fresh, Mode::Each, &mut |turn, score| {
            ranked.push((turn, score));
            Ok(true)
        })?;
        Ok(ranked)
    }

    /// History ranking version 1 (DESIGN §40.43): SQL decides membership with
    /// version 1's `ILIKE` over `search_text`, under the database's collation,
    /// within the selection and bounds. Newest is the SQL answer, ordered and
    /// limited there, without `search_text`, resuming by keyset. Relevance
    /// streams every row with any term and keeps only its facts, counting over
    /// the same stored text, then reads the winners in the same snapshot
    /// (§40.45). Each turn goes to `visit` in answer order (§40.46).
    pub fn rank_each(
        &self,
        request: &Request,
        resume: &Resume,
        mode: Mode,
        visit: &mut Visit,
    ) -> Result<Option<String>, Error> {
        let ids = request.ids.as_deref();
        check_ids(ids)?;
        request.bounds().map_err(Error::Value)?;
        let patterns: Vec<String> = request
            .terms
            .iter()
            .map(|term| format!("%{}%", rows::escaped(term)))
            .collect();
        let mut params = json!({"patterns": patterns, "all": ids.is_none(),
            "ids": ids.unwrap_or_default(), "since": request.since,
            "before": request.before, "limit": request.limit,
            "at": null, "at_id": null, "at_turn": null});
        if request.order == Order::Newest {
            if let Resume::After(Last {
                created,
                conversation,
                turn,
            }) = resume
            {
                params["at"] = json!(Stamp::from_micros(*created).z());
                params["at_id"] = json!(conversation);
                params["at_turn"] = json!(turn);
            }
            self.turn_rows(sql!("store/rank_turns"), &params, mode, |row| {
                visit(
                    rows::turn(&codec::text(&row, "conversation_id")?, row)?,
                    None,
                )
            })?;
            return Ok(None);
        }
        self.rank_relevance(request, &params, resume, mode, visit)
    }

    pub fn append_projections(&self, id: &str, entries: &[Projection]) -> Result<(), Error> {
        parse_conversation_id(id)?;
        if entries.is_empty() {
            return Ok(());
        }
        let entries: Vec<Value> = entries.iter().map(encode::projection).collect();
        self.fetch(
            sql!("store/append_projections"),
            json!({"conversation_id":id,"entries":entries,"now":Stamp::now().z(),"format":FORMAT}),
        )?;
        Ok(())
    }

    pub fn read_projections(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Projection>, Error> {
        parse_conversation_id(id)?;
        rows::window(after, limit)?;
        self.fetch(
            sql!("store/read_projections"),
            json!({"conversation_id":id,"after":after,"limit":limit}),
        )?
        .iter()
        .map(|row| rows::projection(id, row))
        .collect()
    }

    pub fn redact_turns(
        &self,
        id: &str,
        through: Option<i64>,
        turns: Option<&[i64]>,
        actor: Option<&str>,
    ) -> Result<usize, Error> {
        parse_conversation_id(id)?;
        let (through, named) = crate::store::erasure::selection(through, turns)?;
        let row = self.one(sql!("erasure/redact_turns"), json!({
            "conversation_id":id,"through":through,"all":named.is_none(),"turns":named.unwrap_or_default(),
            "actor":actor,"now":Stamp::now().z(),"format":FORMAT
        }), false)?;
        if row["format_ok"] != true {
            return Err(Refused::format_unsupported(
                id,
                &format!(
                    "erasure target declares unsupported format {}",
                    row["current_format"]
                ),
            )
            .into());
        }
        Ok(codec::number(&row, "matched")? as usize)
    }

    pub fn turn_redactions(
        &self,
        ids: Option<&[String]>,
        since: Option<Stamp>,
        limit: i64,
    ) -> Result<Vec<Redaction>, Error> {
        check_ids(ids)?;
        if limit < 1 {
            return Err(Error::Value(format!("limit must be >= 1, got {limit}")));
        }
        self.fetch(sql!("erasure/read_turn_redactions"), json!({"all":ids.is_none(),"ids":ids.unwrap_or_default(),"since":since.map(Stamp::z),"limit":limit}))?
            .iter().map(|row| Ok(codec::turn_redaction(row)?)).collect()
    }
}

fn check_ids(ids: Option<&[String]>) -> Result<(), Error> {
    for id in ids.into_iter().flatten() {
        parse_conversation_id(id)?;
    }
    Ok(())
}
