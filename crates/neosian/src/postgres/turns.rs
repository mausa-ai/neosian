//! The turn ABC, search, projections and erasure over atomic SQL statements.

use super::{PostgresStore, rows, sql};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::search::messages_text;
use crate::conversation::{
    Error, FORMAT, Projection, Redaction, Turn,
    message::{self, Message},
};
use crate::remote::codec;
use crate::store::time::Stamp;
use crate::wire::encode;
use serde_json::{Value, json};

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
        parse_conversation_id(id)?;
        rows::window(after, limit)?;
        self.fetch(
            sql!("store/read_turns"),
            json!({"conversation_id":id,"after":after,"limit":limit}),
        )?
        .into_iter()
        .map(|row| rows::turn(id, row))
        .collect()
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
        check_ids(ids)?;
        if limit < 1 {
            return Err(Error::Value(format!("limit must be >= 1, got {limit}")));
        }
        let patterns: Vec<String> = terms
            .iter()
            .map(|term| format!("%{}%", rows::escaped(term)))
            .collect();
        self.fetch(sql!("store/search_turns"), json!({"patterns":patterns,"all":ids.is_none(),"ids":ids.unwrap_or_default(),"limit":limit}))?
            .into_iter().map(|row| rows::turn(&codec::text(&row, "conversation_id")?, row)).collect()
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
            "actor":actor,"now":Stamp::now().z()
        }), false)?;
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
