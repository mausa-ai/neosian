//! The inbox as a reader sees it (`messaging/core.py`, DESIGN §34): the
//! message documents of the scopes a reader reaches, each held to its
//! address. The unread listing and the lineage land with the record
//! verb's delivery; `send` and `update` with the `messages` tool (#360).

use std::io;

use super::types::{InboxMessage, PREFIX, decode, message_path};
use crate::conversation::Error;
use crate::store::FileStore;

pub struct Receipt {
    pub message: InboxMessage,
    pub version: u64,
}

/// An explicitly scoped mailbox; session identity is never guessed.
pub struct Mailbox<'a> {
    store: &'a FileStore,
    scopes: Vec<String>,
}

impl<'a> Mailbox<'a> {
    /// Over every scope the store holds: the search verb's reach.
    pub fn over_scopes(store: &'a FileStore, scopes: Vec<String>) -> Self {
        Self { store, scopes }
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
}

/// A `ValueError` of the Python side: its sentence, at tier one.
fn value_error(sentence: String) -> Error {
    Error::Io(io::Error::other(sentence))
}
