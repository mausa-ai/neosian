//! The operations used by the record and eight MCP tools, over the frozen
//! wire. Scope/path validation belongs to the server's store.

use super::{RemoteStore, codec};
use crate::conversation::message::{self, Message};
use crate::conversation::{self, Projection, Turn};
use crate::state::{Deleted, State};
use crate::store::error::Error;
use crate::store::grammar::Actor;
use crate::store::journal::Row;
use crate::store::{Document, Entry};
use serde_json::{Value, json};

impl State for RemoteStore {
    fn supports_optimistic_concurrency(&self) -> bool {
        self.capabilities
            .get("supports_optimistic_concurrency")
            .is_some_and(|value| !message::falsy(value))
    }

    fn client(&self) -> Option<&str> {
        self.client.as_deref()
    }

    fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error> {
        let data = self.call("memory/read", json!({"scope": scope, "path": path}))?;
        let document = codec::key(&data, "document")?;
        if document.is_null() {
            Ok(None)
        } else {
            codec::document(document).map(Some)
        }
    }

    fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error> {
        self.follow(
            "memory/list_documents",
            json!({"scope":scope,"prefix":prefix,"cursor":null,"limit":null}),
            "entries",
            false,
        )?
        .iter()
        .map(codec::entry)
        .collect()
    }

    fn versions(&self, scope: &str, path: &str, limit: usize) -> Result<Vec<Row>, Error> {
        self.follow(
            "memory/versions",
            json!({"scope":scope,"path":path,"cursor":null,"limit":limit}),
            "versions",
            false,
        )?
        .iter()
        .map(codec::version)
        .collect()
    }

    fn write(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<u64, Error> {
        let data = self.call("memory/write", json!({"scope":scope,"path":path,"content":content,"actor":actor.as_str(),"expected_version":expected}))?;
        Ok(codec::document(codec::key(&data, "document")?)?
            .envelope
            .version)
    }

    fn delete(&self, scope: &str, path: &str, actor: &Actor) -> Result<Deleted, Error> {
        let data = self.call(
            "memory/delete",
            json!({"scope":scope,"path":path,"actor":actor.as_str()}),
        )?;
        let removed = codec::key(&data, "deleted")?
            .as_bool()
            .ok_or_else(|| codec::malformed("deleted"))?;
        let version = if removed {
            self.versions(scope, path, 1)?
                .first()
                .map(|row| row.version)
        } else {
            None
        };
        Ok(Deleted { removed, version })
    }

    fn rename(&self, scope: &str, src: &str, dst: &str, actor: &Actor) -> Result<u64, Error> {
        let data = self.call(
            "memory/rename",
            json!({"scope":scope,"src":src,"dst":dst,"actor":actor.as_str()}),
        )?;
        Ok(codec::document(codec::key(&data, "document")?)?
            .envelope
            .version)
    }

    fn read_turns(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Turn>, conversation::Error> {
        Ok(self
            .follow(
                "conversation/read_turns",
                json!({"conversation_id":id,"after":after,"limit":limit}),
                "turns",
                true,
            )?
            .iter()
            .map(codec::turn)
            .collect::<Result<_, _>>()?)
    }

    fn read_projections(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Projection>, conversation::Error> {
        Ok(self
            .follow(
                "conversation/read_projections",
                json!({"conversation_id":id,"after":after,"limit":limit}),
                "entries",
                true,
            )?
            .iter()
            .map(codec::projection)
            .collect::<Result<_, _>>()?)
    }

    fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, conversation::Error> {
        let data = self.call(
            "conversation/search_turns",
            json!({"query":terms.join(" "),"conversations":ids,"limit":limit}),
        )?;
        Ok(codec::array(&data, "turns")?
            .iter()
            .map(codec::turn)
            .collect::<Result<_, _>>()?)
    }

    fn append_turn(
        &self,
        id: &str,
        messages: Vec<Message>,
        actor: Option<String>,
    ) -> Result<Turn, conversation::Error> {
        let messages: Vec<Value> = messages.iter().map(message::to_json).collect();
        let data = self.call(
            "conversation/append_turn",
            json!({"conversation_id":id,"messages":messages,"actor":actor}),
        )?;
        Ok(codec::turn(codec::key(&data, "turn")?)?)
    }
}
