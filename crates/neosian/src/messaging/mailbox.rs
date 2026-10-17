//! The inbox (`messaging/core.py`, DESIGN §34): the message documents of
//! the scopes a reader reaches, each held to its address, the lineage a
//! session is addressed through, and the listings. Session identity is
//! never guessed. The writes (`send`, `update`, the lineage note) are in
//! `writes.rs`.

use std::collections::{HashMap, HashSet};

use super::types::{Delivery, InboxMessage, PREFIX, decode, message_path};
use crate::conversation::Error;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::sessions::{continues, sessions_path};
use crate::memory::mounts::Mount;
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

/// `Mailbox.list`'s page, and the ceiling it refuses above.
pub const LIST_LIMIT: usize = 50;
const LIST_MAX: usize = 500;
const DEFAULT_ACTOR: &str = "mailbox:local";

/// Which messages a listing answers with.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ListStatus {
    Unread,
    Open,
    Scheduled,
    Closed,
    All,
}

impl ListStatus {
    pub const ALL: [Self; 5] = [
        Self::Unread,
        Self::Open,
        Self::Scheduled,
        Self::Closed,
        Self::All,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unread => "unread",
            Self::Open => "open",
            Self::Scheduled => "scheduled",
            Self::Closed => "closed",
            Self::All => "all",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|status| status.as_str() == text)
    }
}

#[derive(Debug)]
pub struct Receipt {
    pub message: InboxMessage,
    pub version: u64,
}

/// One scope a mailbox reaches, with the policy of the mount that gave it.
#[derive(Clone)]
pub(super) struct Reach {
    pub scope: String,
    pub path: String,
    pub read_only: bool,
    pub edit_only: bool,
}

/// An explicitly scoped mailbox; session identity is never guessed.
pub struct Mailbox<'a> {
    pub(super) store: &'a FileStore,
    pub(super) reach: Vec<Reach>,
    pub(super) session: Option<String>,
    pub(super) actor: Actor,
    clock: Option<Stamp>,
}

impl<'a> Mailbox<'a> {
    fn new(store: &'a FileStore, reach: Vec<Reach>, session: Option<&str>) -> Self {
        Self {
            store,
            reach,
            session: session.map(str::to_string),
            actor: Actor::parse(DEFAULT_ACTOR).expect("the default actor is grammatical"),
            clock: None,
        }
    }

    /// Over the mounts' scopes, each once, in mount order: a hook's reach.
    pub fn of_mounts(store: &'a FileStore, mounts: &[Mount], session: Option<&str>) -> Self {
        let mut reach: Vec<Reach> = Vec::new();
        for mount in mounts {
            if !reach.iter().any(|known| known.scope == mount.scope) {
                reach.push(Reach {
                    scope: mount.scope.clone(),
                    path: mount.path.clone(),
                    read_only: mount.read_only,
                    edit_only: mount.edit_only,
                });
            }
        }
        Self::new(store, reach, session)
    }

    /// Over every scope the store holds: the search verb's reach, reads alone.
    pub fn over_scopes(store: &'a FileStore, scopes: Vec<String>) -> Self {
        let reach = scopes
            .into_iter()
            .map(|scope| Reach {
                scope,
                path: String::new(),
                read_only: true,
                edit_only: false,
            })
            .collect();
        Self::new(store, reach, None)
    }

    /// Who the writes are recorded by.
    pub fn with_actor(mut self, actor: Actor) -> Self {
        self.actor = actor;
        self
    }

    /// A fixed clock (the tests'); the wall clock otherwise, read per call.
    #[cfg(test)]
    pub fn at(mut self, now: Stamp) -> Self {
        self.clock = Some(now);
        self
    }

    pub fn session(&self) -> Option<&str> {
        self.session.as_deref()
    }

    /// The same reach and clock read as another session.
    pub fn as_session(&self, session: Option<&str>) -> Self {
        Self {
            store: self.store,
            reach: self.reach.clone(),
            session: session.map(str::to_string),
            actor: self.actor.clone(),
            clock: self.clock,
        }
    }

    pub fn now(&self) -> Stamp {
        self.clock.unwrap_or_else(Stamp::now)
    }

    /// The scope's mount, or the Python side's refusals: not mounted, and
    /// read-only when a write asks.
    pub(super) fn mounted(&self, scope: &str, write: bool) -> Result<&Reach, Error> {
        let Some(reach) = self.reach.iter().find(|reach| reach.scope == scope) else {
            return Err(Error::Value(format!("scope is not mounted: {scope}")));
        };
        if write && reach.read_only {
            return Err(MemoryError::read_only(&reach.path).into());
        }
        Ok(reach)
    }

    /// One message read back and held to its address; absent or redacted
    /// is the document-not-found refusal.
    pub fn view(&self, scope: &str, id: &str) -> Result<Receipt, Error> {
        self.mounted(scope, false)?;
        let path = message_path(id).map_err(Error::Value)?;
        let document = self
            .store
            .read(scope, &path)?
            .filter(|document| !document.envelope.redacted)
            .ok_or_else(|| MemoryError::not_found(scope, &path))?;
        let message = decode(&document.content).map_err(Error::Value)?;
        if message.scope != scope || message_path(&message.id).map_err(Error::Value)? != path {
            let mismatch = "message envelope does not match its storage address";
            return Err(Error::Value(mismatch.to_string()));
        }
        Ok(Receipt {
            message,
            version: document.envelope.version,
        })
    }

    /// Every version of one message, newest first, the redacted skipped.
    pub fn history(&self, scope: &str, id: &str) -> Result<Vec<InboxMessage>, Error> {
        let receipt = self.view(scope, id)?;
        let path = message_path(id).map_err(Error::Value)?;
        let rows = self
            .store
            .versions(scope, &path, receipt.version as usize)?;
        rows.iter()
            .filter(|row| !row.redacted)
            .map(|row| decode(&row.content).map_err(Error::Value))
            .collect()
    }

    /// Every live message of one scope, or of every scope reached, by
    /// creation then id; one deleted between the listing and its read is
    /// skipped.
    pub fn all(&self, scope: Option<&str>) -> Result<Vec<Receipt>, Error> {
        let scopes: Vec<&str> = match scope {
            Some(scope) => vec![scope],
            None => self
                .reach
                .iter()
                .map(|reach| reach.scope.as_str())
                .collect(),
        };
        let mut found = Vec::new();
        for selected in scopes {
            self.mounted(selected, false)?;
            for entry in self.store.list_documents(selected, PREFIX)? {
                if entry.redacted {
                    continue;
                }
                match self.view(selected, &entry.path[PREFIX.len()..]) {
                    Ok(receipt) => found.push(receipt),
                    Err(Error::Memory(error)) if error.code == "memory_document_not_found" => {}
                    Err(error) => return Err(error),
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
        self.mounted(scope, false)?;
        let mut pending: Vec<String> = self.session.iter().cloned().collect();
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

    /// The messages a listing answers with, the earliest due first, at
    /// most `limit`: an addressed message only through lineage, and under
    /// `wake_only` the wake deliveries still pending.
    pub fn list(
        &self,
        scope: Option<&str>,
        status: ListStatus,
        limit: usize,
        addressed: bool,
        wake_only: bool,
    ) -> Result<Vec<Receipt>, Error> {
        if !(1..=LIST_MAX).contains(&limit) {
            return Err(Error::Value("limit must be between 1 and 500".to_string()));
        }
        let now = self.now();
        let mut lineage: HashMap<String, HashSet<String>> = HashMap::new();
        let mut found = Vec::new();
        for receipt in self.all(scope)? {
            let item = &receipt.message;
            if wake_only
                && (item.delivery != Delivery::Wake
                    || item.wake_accepted
                    || !item.unread(now)
                    || item.claimed(now)
                    || item
                        .wake_until
                        .is_some_and(|until| until.instant() > now.instant()))
            {
                continue;
            }
            if addressed && let Some(conversation) = &item.conversation {
                if !lineage.contains_key(&item.scope) {
                    let reach = self.lineage(&item.scope)?;
                    lineage.insert(item.scope.clone(), reach);
                }
                if !lineage[&item.scope].contains(conversation) {
                    continue;
                }
            }
            let matches = match status {
                ListStatus::All => true,
                ListStatus::Unread => item.unread(now),
                ListStatus::Open => item.available(now),
                ListStatus::Scheduled => {
                    item.status == "open" && item.due_at.instant() > now.instant()
                }
                ListStatus::Closed => item.status != "open",
            };
            if matches {
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
        found.truncate(limit);
        Ok(found)
    }
}
