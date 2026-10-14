//! The inbox as a reader sees it (`messaging/core.py`, DESIGN §34): the
//! message documents of the scopes a reader reaches, each held to its
//! address, the lineage a session is addressed through, and the unread
//! listing the hooks and the search verb deliver. `send` and `update`
//! land with the `messages` tool (#360).

use std::collections::{HashMap, HashSet};
use std::io;

use super::types::{InboxMessage, PREFIX, decode, message_path};
use crate::conversation::Error;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::sessions::{continues, sessions_path};
use crate::memory::mounts::Mount;
use crate::store::FileStore;
use crate::store::time::Stamp;

/// `Mailbox.list`'s page.
const LIST_LIMIT: usize = 50;

pub struct Receipt {
    pub message: InboxMessage,
    pub version: u64,
}

/// An explicitly scoped mailbox; session identity is never guessed.
pub struct Mailbox<'a> {
    store: &'a FileStore,
    scopes: Vec<String>,
    session: Option<&'a str>,
}

impl<'a> Mailbox<'a> {
    /// Over the mounts' scopes, each once, in mount order: a hook's reach.
    pub fn of_mounts(store: &'a FileStore, mounts: &[Mount], session: Option<&'a str>) -> Self {
        let mut scopes: Vec<String> = Vec::new();
        for mount in mounts {
            if !scopes.contains(&mount.scope) {
                scopes.push(mount.scope.clone());
            }
        }
        Self {
            store,
            scopes,
            session,
        }
    }

    /// Over every scope the store holds: the search verb's reach.
    pub fn over_scopes(store: &'a FileStore, scopes: Vec<String>) -> Self {
        Self {
            store,
            scopes,
            session: None,
        }
    }

    pub fn session(&self) -> Option<&str> {
        self.session
    }

    /// One message read back and held to its address; None when the
    /// document is absent or redacted.
    pub fn view(&self, scope: &str, id: &str) -> Result<Option<Receipt>, Error> {
        let path = message_path(id).map_err(value_error)?;
        let Some(document) = self.store.read(scope, &path)? else {
            return Ok(None);
        };
        if document.envelope.redacted {
            return Ok(None);
        }
        let message = decode(&document.content).map_err(value_error)?;
        if message.scope != scope || message_path(&message.id).map_err(value_error)? != path {
            let mismatch = "message envelope does not match its storage address";
            return Err(value_error(mismatch.to_string()));
        }
        Ok(Some(Receipt {
            message,
            version: document.envelope.version,
        }))
    }

    /// Every version of one message, newest first, the redacted skipped.
    pub fn history(&self, scope: &str, receipt: &Receipt) -> Result<Vec<InboxMessage>, Error> {
        let path = message_path(&receipt.message.id).map_err(value_error)?;
        let rows = self
            .store
            .versions(scope, &path, receipt.version as usize)?;
        rows.iter()
            .filter(|row| !row.redacted)
            .map(|row| decode(&row.content).map_err(value_error))
            .collect()
    }

    /// Every live message of the scopes, by creation then id.
    pub fn all(&self) -> Result<Vec<Receipt>, Error> {
        let mut found = Vec::new();
        for scope in &self.scopes {
            for entry in self.store.list_documents(scope, PREFIX)? {
                if entry.redacted {
                    continue;
                }
                if let Some(receipt) = self.view(scope, &entry.path[PREFIX.len()..])? {
                    found.push(receipt);
                }
            }
        }
        found.sort_by(|a, b| {
            (a.message.created_at.instant(), &a.message.id)
                .cmp(&(b.message.created_at.instant(), &b.message.id))
        });
        Ok(found)
    }

    /// The sessions this one succeeds, itself included: every `continues`
    /// line of a sessions document and every immediate `message-links/`
    /// declaration, which can precede the recorder's Stop.
    pub fn lineage(&self, scope: &str) -> Result<HashSet<String>, Error> {
        let mut pending: Vec<String> = self.session.iter().map(|s| s.to_string()).collect();
        let mut found = HashSet::new();
        while let Some(current) = pending.pop() {
            if !found.insert(current.clone()) {
                continue;
            }
            if let Some(document) = self.store.read(scope, &sessions_path(&current))?
                && !document.envelope.redacted
            {
                for parent in continues(&document.content) {
                    parse_conversation_id(&parent)?;
                    pending.push(parent);
                }
            }
            let prefix = format!("message-links/{current}/");
            for entry in self.store.list_documents(scope, &prefix)? {
                if !entry.redacted {
                    let parent = entry.path[prefix.len()..].to_string();
                    parse_conversation_id(&parent)?;
                    pending.push(parent);
                }
            }
        }
        Ok(found)
    }

    /// The unread, due messages this session may read, the earliest due
    /// first, at most a page: an addressed message only through lineage.
    pub fn list(&self) -> Result<Vec<Receipt>, Error> {
        let now = Stamp::now();
        let mut lineage: HashMap<String, HashSet<String>> = HashMap::new();
        let mut found = Vec::new();
        for receipt in self.all()? {
            let item = &receipt.message;
            if let Some(conversation) = &item.conversation {
                if !lineage.contains_key(&item.scope) {
                    let reach = self.lineage(&item.scope)?;
                    lineage.insert(item.scope.clone(), reach);
                }
                if !lineage[&item.scope].contains(conversation) {
                    continue;
                }
            }
            if item.unread(now) {
                found.push(receipt);
            }
        }
        found.sort_by_key(|receipt| {
            let item = &receipt.message;
            (
                item.due_at.instant(),
                item.created_at.instant(),
                item.id.clone(),
            )
        });
        found.truncate(LIST_LIMIT);
        Ok(found)
    }
}

/// A `ValueError` of the Python side: its sentence, at tier one.
fn value_error(sentence: String) -> Error {
    Error::Io(io::Error::other(sentence))
}
