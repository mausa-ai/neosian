//! Verbatim archives, one atomic statement per scope or conversation.

use super::{PostgresStore, sql};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::search::turn_text;
use crate::conversation::{self, Projection, Redaction, Refused, Turn};
use crate::remote::codec;
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::parse_scope;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{FORMAT, Restored};
use crate::wire::encode;
use serde_json::{Value, json};

impl PostgresStore {
    pub fn scopes(&self) -> Result<Vec<String>, Error> {
        self.fetch(sql!("portable/list_scopes"), json!({}))?
            .iter()
            .map(|row| codec::text(row, "scope"))
            .collect()
    }

    pub fn conversations(&self) -> Result<Vec<String>, Error> {
        self.fetch(sql!("portable/list_conversations"), json!({}))?
            .iter()
            .map(|row| codec::text(row, "conversation_id"))
            .collect()
    }

    pub fn restore_scope(
        &self,
        scope: &str,
        documents: &[Restored],
        versions: Vec<Row>,
        redactions: &[Act],
    ) -> Result<(), Error> {
        parse_scope(scope)?;
        let documents: Vec<Value> = documents
            .iter()
            .map(|doc| encode::document_parts(scope, &doc.path, &doc.content, &doc.envelope))
            .collect::<Result<_, _>>()?;
        let versions: Vec<Value> = versions.iter().map(encode::version).collect();
        let redactions: Vec<Value> = redactions.iter().map(encode::redaction).collect();
        let row = self.one(sql!("portable/restore_scope"), json!({"scope":scope,"documents":documents,"versions":versions,"redactions":redactions,"format":FORMAT}), true)?;
        if row["empty"] != true {
            return Err(MemoryError::conflict(scope, None, "target_occupied").into());
        }
        Ok(())
    }

    pub fn restore_conversation(
        &self,
        id: &str,
        turns: &[Turn],
        projections: &[Projection],
        redactions: &[Redaction],
    ) -> Result<(), conversation::Error> {
        parse_conversation_id(id)?;
        let first = turns
            .first()
            .map_or_else(Stamp::now, |turn| turn.created_at);
        let turns: Vec<Value> = turns
            .iter()
            .map(|turn| {
                let mut row = encode::turn(turn);
                row["search_text"] = turn_text(turn).into();
                row
            })
            .collect();
        let projections: Vec<Value> = projections.iter().map(encode::projection).collect();
        let redactions: Vec<Value> = redactions.iter().map(encode::turn_redaction).collect();
        let row = self.one(sql!("portable/restore_conversation"), json!({"conversation_id":id,"now":first.z(),"turns":turns,"projections":projections,"redactions":redactions,"format":conversation::FORMAT}), true)?;
        if row["empty"] != true {
            return Err(Refused::conflict(id, "target_occupied").into());
        }
        Ok(())
    }
}
