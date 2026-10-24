//! The product doors over the frozen wire. Scope/path validation
//! belongs to the server's store.

use super::{RemoteStore, codec};
use crate::conversation::message::{self, Message};
use crate::conversation::{self, Projection, Redaction, Turn};
use crate::state::{Deleted, State};
use crate::store::error::Error;
use crate::store::grammar::Actor;
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, Restored};
use crate::wire::encode;
use serde_json::{Value, json};

impl State for RemoteStore {
    fn supports_changes(&self) -> Result<bool, Error> {
        Ok(self.capabilities["change_feed"]["version"] == 1)
    }
    fn read_changes(
        &self,
        scope: &str,
        request: &crate::changes::model::Request,
    ) -> Result<crate::changes::model::Page, Error> {
        self.changes_read(scope, request)
    }
    fn prune_changes(
        &self,
        scope: &str,
        through: &str,
        write: bool,
    ) -> Result<crate::changes::model::Pruned, Error> {
        self.changes_prune(scope, through, write)
    }
    fn search_memory(
        &self,
        scope: &str,
        request: &crate::memory::search::Request,
    ) -> Result<crate::memory::search::Page, Error> {
        request.validate_backend(scope)?;
        if self.capabilities["memory_search"]["version"] != 1 {
            return Err(crate::store::error::MemoryError::configuration(
                "server does not support memory-search version 1",
            )
            .into());
        }
        if !request.knowledge_policy.is_all()
            && self.capabilities["knowledge_policy"]["version"] != 1
        {
            return Err(crate::store::error::MemoryError::configuration(
                "server does not support knowledge-policy version 1",
            )
            .into());
        }
        let mut payload = serde_json::to_value(request).expect("search request serializes");
        payload["scope"] = scope.into();
        let page = serde_json::from_value(self.call("memory/search", payload)?)
            .map_err(|_| codec::malformed("memory/search"))?;
        request.check_page(scope, &page)?;
        Ok(page)
    }
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
    fn history(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Row>, Error> {
        self.follow(
            "memory/history",
            json!({"scope":scope,"since":since.map(|stamp| stamp.utc().z()),"cursor":null,"limit":limit}),
            "versions",
            false,
        )?
        .iter()
        .map(codec::version)
        .collect()
    }
    fn redactions(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Act>, Error> {
        self.follow(
            "memory/redactions",
            json!({"scope":scope,"since":since.map(|stamp| stamp.utc().z()),"cursor":null,"limit":limit}),
            "redactions",
            false,
        )?
        .iter()
        .map(codec::redaction)
        .collect()
    }
    fn redact(&self, scope: &str, path: Option<&str>, actor: &Actor) -> Result<usize, Error> {
        let data = self.call(
            "memory/redact",
            json!({"scope":scope,"path":path,"actor":actor.as_str()}),
        )?;
        Ok(codec::number(&data, "count")? as usize)
    }
    fn scopes(&self) -> Result<Vec<String>, Error> {
        self.names("scopes")
    }
    fn conversations(&self) -> Result<Vec<String>, Error> {
        self.names("conversations")
    }
    fn last_turn_number(&self, id: &str) -> Result<u64, conversation::Error> {
        let data = self.call(
            "conversation/last_turn_number",
            json!({"conversation_id":id}),
        )?;
        Ok(codec::number(&data, "turn")?)
    }
    fn restore_scope(
        &self,
        scope: &str,
        documents: &[Restored],
        versions: Vec<Row>,
        redactions: &[Act],
    ) -> Result<(), Error> {
        let documents = documents
            .iter()
            .map(|doc| encode::document_parts(scope, &doc.path, &doc.content, &doc.envelope))
            .collect::<Result<Vec<_>, _>>()?;
        self.call(
            "store/restore_scope",
            json!({
                "scope": scope,
                "documents": documents,
                "versions": versions.iter().map(encode::version).collect::<Vec<_>>(),
                "redactions": redactions.iter().map(encode::redaction).collect::<Vec<_>>(),
            }),
        )?;
        Ok(())
    }
    fn restore_conversation(
        &self,
        id: &str,
        turns: &[Turn],
        projections: &[Projection],
        redactions: &[Redaction],
    ) -> Result<(), conversation::Error> {
        self.call(
            "store/restore_conversation",
            json!({
                "conversation_id": id,
                "turns": turns.iter().map(encode::turn).collect::<Vec<_>>(),
                "projections": projections.iter().map(encode::projection).collect::<Vec<_>>(),
                "redactions": redactions.iter().map(encode::turn_redaction).collect::<Vec<_>>(),
            }),
        )?;
        Ok(())
    }
    fn redact_turns(
        &self,
        id: &str,
        through: Option<i64>,
        turns: Option<&[i64]>,
        actor: Option<&str>,
    ) -> Result<usize, conversation::Error> {
        self.require_erasable()?;
        let data = self.call(
            "conversation/redact_turns",
            json!({"conversation_id":id,"through":through,"turns":turns,"actor":actor}),
        )?;
        Ok(codec::number(&data, "count")? as usize)
    }
    fn turn_redactions(
        &self,
        ids: Option<&[String]>,
        since: Option<Stamp>,
        limit: i64,
    ) -> Result<Vec<Redaction>, conversation::Error> {
        self.require_erasable()?;
        let data = self.call(
            "conversation/turn_redactions",
            json!({"conversations":ids,"since":since.map(|stamp| stamp.utc().z()),"limit":limit}),
        )?;
        Ok(codec::array(&data, "redactions")?
            .iter()
            .map(codec::turn_redaction)
            .collect::<Result<_, _>>()?)
    }
}
