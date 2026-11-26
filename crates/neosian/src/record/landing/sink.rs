//! What a queued turn is written to (DESIGN §40.54 items 7, 9 and 17.4): a
//! FileStore or a PostgresStore, through their own records methods when
//! they keep records and with `append_turn` when they do not; a URL or a
//! profile with `append_turn`, until the remote landing of T1e1c. Each
//! answer to a write is one `Answer`: the draft's state, or a refusal with
//! the status and code `neosian serve` answers for the same store error
//! (`serve/envelope.rs`), so the rules of 17.4 read a local store and a
//! server alike. A local failure to read or write a file is no answer: it
//! stops the run, and the turn stays queued.

use std::sync::Arc;

use serde_json::json;

use crate::conversation::message::Message;
use crate::conversation::turn_records::{Write, Written};
use crate::conversation::{Error, Turn};
use crate::limits::{self, RECORD_TURN_BYTES};
use crate::postgres::PostgresStore;
use crate::remote::records::Refusal;
use crate::remote::selection::Selection;
use crate::state::State;
use crate::store::FileStore;

/// The code of a refused value: a record, a part or the turn.
pub const VALUE_ERROR: &str = "value_error";

/// One write's answer, as the rules of 17.4 read it.
pub enum Answer {
    /// The store took the request: the draft's state, `next_seq` and,
    /// once sealed, the turn's header.
    Written(Written),
    /// The store refused it, nothing of the request stored.
    Refused(Refusal),
}

pub enum Sink {
    File(FileStore),
    Postgres(Box<PostgresStore>),
    /// A URL or a profile: `append_turn` only, in this slice.
    Other(Arc<dyn State>),
}

/// A store that keeps records, with the limits read for it.
pub struct Records<'a> {
    store: Local<'a>,
    limits: limits::Records,
}

enum Local<'a> {
    File(&'a FileStore),
    Postgres(&'a PostgresStore),
}

impl Sink {
    /// The store `selection` names, opened once for a run.
    pub fn open(selection: &Selection) -> Result<Self, Error> {
        Ok(match selection {
            Selection::File(root) => Self::File(FileStore::open(root)?),
            Selection::Postgres { dsn, schema } => {
                Self::Postgres(Box::new(PostgresStore::open(dsn, schema)?))
            }
            other => Self::Other(other.open()?),
        })
    }

    pub fn state(&self) -> &dyn State {
        match self {
            Self::File(store) => store,
            Self::Postgres(store) => store.as_ref(),
            Self::Other(store) => store.as_ref(),
        }
    }

    /// The records side, when this store keeps records now: a FileStore
    /// after `neosian records enable`, Postgres with its extension. The
    /// record settings are read only then.
    pub fn records(&self) -> Result<Option<Records<'_>>, Error> {
        let store = match self {
            Self::File(store) if store.records_enabled()? => Local::File(store),
            Self::Postgres(store) if store.supports_records()? => Local::Postgres(store),
            _ => return Ok(None),
        };
        let limits = limits::Records::load()?;
        Ok(Some(Records { store, limits }))
    }

    /// The turn whole, as every store takes it.
    pub fn append_turn(
        &self,
        id: &str,
        messages: Vec<Message>,
        actor: String,
    ) -> Result<Turn, Error> {
        self.state().append_turn(id, messages, Some(actor))
    }
}

impl Records<'_> {
    /// One request of a draft: a probe, a part, a seal or a discard. The
    /// actor is the foreign actor as `append_turn` stores it, unstamped.
    pub fn write(&self, id: &str, write: Write) -> Result<Answer, Error> {
        let result = match self.store {
            Local::File(store) => store.write_records(id, write, &self.limits),
            Local::Postgres(store) => store.write_records(id, write, &self.limits),
        };
        answer(result)
    }
}

/// A local store's answer as `neosian serve` would send it: a refusal at
/// 400 with its code and details, a bare sentence as `value_error`, at 413
/// when it is the store's bound on a turn (item 8.2).
fn answer(result: Result<Written, Error>) -> Result<Answer, Error> {
    let (status, code, message, details) = match result {
        Ok(written) => return Ok(Answer::Written(written)),
        Err(Error::Conversation(refused)) => (400, refused.code, refused.message, refused.details),
        Err(Error::Memory(error)) => (400, error.code, error.message, error.details),
        Err(Error::Value(sentence)) => {
            let bound = format!("resource limit {RECORD_TURN_BYTES} exceeded: ");
            let status = if sentence.starts_with(&bound) {
                413
            } else {
                400
            };
            (status, VALUE_ERROR, sentence, json!({}))
        }
        Err(error @ Error::Io(_)) => return Err(error),
    };
    Ok(Answer::Refused(Refusal {
        status,
        retry_after: None,
        code: Some(code.to_string()),
        message,
        details,
    }))
}
