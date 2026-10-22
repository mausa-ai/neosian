//! The mailbox's writes (`messaging/core.py`'s `send` and `update`,
//! `history.py`'s `note_continuation`, DESIGN §34): a message is a
//! document the store versions, a lifecycle move lands against the
//! version it read and retries a conflict twice, and a continuation is
//! declared as a `message-links/` document so an addressed message
//! reaches its successor before the recorder's Stop.

use uuid::Uuid;

use super::mailbox::{Mailbox, Receipt};
use super::transitions::{Move, transition};
use super::types::{Action, Delivery, InboxMessage, check_target, due_time, encode, message_path};
use crate::conversation::Error;
use crate::conversation::ids::parse_conversation_id;
use crate::store::error::Error as StoreError;
use crate::store::time::Stamp;

/// What a `send` names beside the body.
pub struct Send<'a> {
    pub scope: &'a str,
    pub conversation: Option<&'a str>,
    pub turn: Option<i64>,
    pub delivery: Delivery,
    pub actionable: bool,
    pub due_at: Option<Stamp>,
    pub delay_seconds: Option<i64>,
    pub about: Option<&'a str>,
    pub about_turn: Option<i64>,
}

/// What a lifecycle `update` names beside the action.
pub struct Update<'a> {
    pub occurrence: i64,
    pub token: Option<&'a str>,
    pub outcome: Option<&'a str>,
    pub due_at: Option<Stamp>,
    pub delay_seconds: Option<i64>,
}

impl Mailbox<'_> {
    /// A new message in `scope`, version 1; the target was checked by
    /// the caller (`check_target`), the annotation is checked here.
    pub fn send(&self, body: &str, send: &Send) -> Result<Receipt, Error> {
        let reach = self.mounted(send.scope, true)?;
        if reach.edit_only {
            return Err(crate::store::error::MemoryError::edit_only(&reach.path).into());
        }
        if body.trim().is_empty() {
            return Err(Error::Value("message body must not be empty".to_string()));
        }
        check_target(send.scope, send.about, send.about_turn).map_err(Error::Value)?;
        let now = self.now().utc();
        let message = InboxMessage {
            id: Uuid::new_v4().to_string(),
            scope: send.scope.to_string(),
            conversation: send.conversation.map(str::to_string),
            turn: send.turn,
            source: self.session.clone(),
            actor: self.actor.as_str().to_string(),
            body: body.to_string(),
            created_at: now,
            due_at: due_time(now, send.due_at, send.delay_seconds).map_err(Error::Value)?,
            delivery: send.delivery,
            actionable: send.actionable || send.due_at.is_some() || send.delay_seconds.is_some(),
            about: send.about.or(send.conversation).map(str::to_string),
            about_turn: if send.about.is_some() {
                send.about_turn
            } else {
                send.turn
            },
            occurrence: 1,
            status: "open".to_string(),
            read_by: None,
            read_at: None,
            owner: None,
            claim_token: None,
            claim_until: None,
            outcome: None,
            wake_owner: None,
            wake_token: None,
            wake_until: None,
            wake_accepted: false,
        };
        let path = message_path(&message.id).map_err(Error::Value)?;
        let version = self
            .store
            .write(send.scope, &path, &encode(&message), &self.actor, None)?;
        Ok(Receipt { message, version })
    }

    /// One lifecycle move by this session, written against the version
    /// read; a conflict is read again, twice. An addressed message is
    /// only a recipient's or a successor's, except that its sender may
    /// withdraw it.
    pub fn update(
        &self,
        scope: &str,
        id: &str,
        action: Action,
        update: &Update,
    ) -> Result<Receipt, Error> {
        self.mounted(scope, true)?;
        let Some(session) = self.session.as_deref() else {
            return Err(Error::Value(
                "an explicit calling session is required".to_string(),
            ));
        };
        if !self.store.supports_optimistic_concurrency() {
            return Err(crate::store::error::MemoryError::configuration(
                "mailbox updates require cross-worker optimistic concurrency",
            )
            .into());
        }
        let due_at = if action == Action::Snooze {
            Some(due_time(self.now(), update.due_at, update.delay_seconds).map_err(Error::Value)?)
        } else {
            None
        };
        for attempt in 0..3 {
            let receipt = self.view(scope, id)?;
            let item = &receipt.message;
            if let Some(conversation) = &item.conversation
                && !self.lineage(scope)?.contains(conversation)
                && (action != Action::Cancel || item.source.as_deref() != Some(session))
            {
                let refusal = "this session is not a recipient or explicit successor";
                return Err(Error::Value(refusal.to_string()));
            }
            let moved = Move {
                session,
                occurrence: update.occurrence,
                token: update.token,
                now: self.now().utc(),
                outcome: update.outcome,
                due_at,
            };
            let changed = transition(item, action, &moved).map_err(Error::Value)?;
            if changed == *item {
                return Ok(receipt);
            }
            let path = message_path(id).map_err(Error::Value)?;
            match self.store.write(
                scope,
                &path,
                &encode(&changed),
                &self.actor,
                Some(receipt.version),
            ) {
                Ok(version) => {
                    return Ok(Receipt {
                        message: changed,
                        version,
                    });
                }
                Err(StoreError::Memory(error))
                    if error.code == "memory_conflict" && attempt < 2 => {}
                Err(error) => return Err(error.into()),
            }
        }
        unreachable!("the third conflict is returned")
    }

    /// This session declared that it continues `parent`: a
    /// `message-links/<session>/<parent>` document, once, so the parent's
    /// messages reach it now; nothing without a session or in an
    /// edit-only mount.
    pub fn note_continuation(&self, scope: &str, parent: &str) -> Result<(), Error> {
        let Some(session) = self.session.as_deref() else {
            return Ok(());
        };
        parse_conversation_id(parent)?;
        let reach = self.mounted(scope, true)?;
        if reach.edit_only {
            return Ok(());
        }
        let path = format!("message-links/{session}/{parent}");
        if self.store.read(scope, &path)?.is_none() {
            self.store.write(scope, &path, parent, &self.actor, None)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
