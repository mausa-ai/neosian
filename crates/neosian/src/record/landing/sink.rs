//! What a queued turn is written to (DESIGN §40.54 items 7, 9, 15.2 and
//! 17.4): a FileStore, a PostgresStore, a server, or a profile's history
//! source. A store that keeps records now (a FileStore after `neosian
//! records enable`, Postgres with its extension, a server whose handshake
//! declared turn records version 1) takes a turn through its own records
//! methods; any other takes it whole as one `append_turn`, a server's
//! through the status-keeping call of `remote/records/append.rs`. Every
//! answer is an `Answer` (`answer.rs`), so the rules of 17.4 read them
//! alike. A local failure to read or write a file is no answer: it stops
//! the run, and the turn stays queued.

use std::sync::Arc;

use super::answer::{self, Answer};
use crate::conversation::message::Message;
use crate::conversation::turn_records::Write;
use crate::conversation::{Error, Turn};
use crate::limits;
use crate::postgres::PostgresStore;
use crate::profile::files::Endpoint;
use crate::profile::model::CaptureMode;
use crate::profile::resolve::Resolved;
use crate::remote::RemoteStore;
use crate::remote::records::Failure;
use crate::remote::selection::Selection;
use crate::state::State;
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::time::Stamp;

pub enum Sink {
    File(FileStore),
    Postgres(Box<PostgresStore>),
    Remote(Box<RemoteStore>),
    /// A profile: its routed store, through which the sessions document is
    /// written as every profile write is, and its history source opened
    /// alone for the turn, with the conversations it may take.
    Profile {
        routed: Arc<dyn State>,
        history: Box<Sink>,
        admits: Box<dyn Fn(&str) -> bool>,
    },
}

/// What became of a turn sent whole.
pub enum Appended {
    Stored(Turn),
    /// The answer that refused or lost it.
    Not(Answer),
}

/// A store that keeps records now, with the record limits a local store
/// is written under.
pub struct Records<'a> {
    keeper: Keeper<'a>,
}

enum Keeper<'a> {
    File(&'a FileStore, limits::Records),
    Postgres(&'a PostgresStore, limits::Records),
    Remote(&'a RemoteStore),
}

impl Sink {
    /// The store `selection` names, opened once for a run.
    pub fn open(selection: &Selection) -> Result<Self, Error> {
        Ok(match selection {
            Selection::File(root) => Self::File(FileStore::open(root)?),
            Selection::Postgres { dsn, schema } => {
                Self::Postgres(Box::new(PostgresStore::open(dsn, schema)?))
            }
            Selection::Remote { url, token } => {
                Self::Remote(Box::new(RemoteStore::connect(url, token)?))
            }
            Selection::Profile(resolved) => profile(resolved)?,
        })
    }

    /// The store the sessions document is written to and read from.
    pub fn state(&self) -> &dyn State {
        match self {
            Self::File(store) => store,
            Self::Postgres(store) => store.as_ref(),
            Self::Remote(store) => store.as_ref(),
            Self::Profile { routed, .. } => routed.as_ref(),
        }
    }

    /// The store a turn is written to: a profile's history source.
    fn target(&self) -> &Sink {
        match self {
            Self::Profile { history, .. } => history.target(),
            other => other,
        }
    }

    /// Refuses a conversation outside a profile's history, or while its
    /// capture is off, as the profile's routing refuses `append_turn`.
    pub fn admit(&self, id: &str) -> Result<(), Error> {
        match self {
            Self::Profile { admits, .. } if !admits(id) => {
                Err(MemoryError::configuration("operation is outside the selected profile").into())
            }
            _ => Ok(()),
        }
    }

    /// The records side, when the store keeps records now. A local store's
    /// record settings are read only then.
    pub fn records(&self) -> Result<Option<Records<'_>>, Error> {
        let keeper = match self.target() {
            Self::File(store) if store.records_enabled()? => {
                Keeper::File(store, limits::Records::load()?)
            }
            Self::Postgres(store) if store.supports_records()? => {
                Keeper::Postgres(store, limits::Records::load()?)
            }
            Self::Remote(store) if store.supports_records() => Keeper::Remote(store),
            _ => return Ok(None),
        };
        Ok(Some(Records { keeper }))
    }

    /// The turn whole, as one `append_turn` by `actor`.
    pub fn append(
        &self,
        id: &str,
        messages: Vec<Message>,
        actor: String,
    ) -> Result<Appended, Error> {
        if let Self::Remote(store) = self.target() {
            return Ok(match store.append_turn_kept(id, &messages, Some(&actor)) {
                Ok(turn) => Appended::Stored(turn),
                Err(failure) => Appended::Not(answer::remote(failure)),
            });
        }
        match self.target().state().append_turn(id, messages, Some(actor)) {
            Ok(turn) => Ok(Appended::Stored(turn)),
            Err(error) => Ok(Appended::Not(Answer::Refused(answer::refused(error)?))),
        }
    }

    /// The time of the conversation's first turn for its sessions document
    /// (ruling D7): from that turn's header on a server that keeps records,
    /// else as the store reads it (one line on a FileStore). A header read
    /// the server refuses (a whole first turn records cannot carry, wire.md
    /// "A turn that cannot be records") is read as before (ruling E16).
    pub fn first_turn_stamp(&self, id: &str) -> Result<Option<Stamp>, Error> {
        if let Self::Remote(store) = self.target()
            && store.supports_records()
        {
            match store.first_turn_time(id) {
                Ok(first) => return Ok(first),
                Err(Failure::Refused(_)) => {}
                Err(failure) => return Err(Error::Value(failure.to_string())),
            }
        }
        self.state().first_turn_stamp(id)
    }
}

impl Records<'_> {
    /// One request of a draft: a probe, a part, a seal or a discard. A
    /// local store takes the foreign actor unstamped, as `append_turn`
    /// stores it; a server stamps the named actor itself.
    pub fn write(&self, id: &str, write: Write) -> Result<Answer, Error> {
        match &self.keeper {
            Keeper::File(store, limits) => answer::local(store.write_records(id, write, limits)),
            Keeper::Postgres(store, limits) => {
                answer::local(store.write_records(id, write, limits))
            }
            Keeper::Remote(store) => Ok(match store.write_records(id, &write) {
                Ok(written) => Answer::Written(written),
                Err(failure) => answer::remote(failure),
            }),
        }
    }
}

/// A profile's routed store, and its history source opened alone as
/// `profile/routing.rs` opens each source: one function T1e2's routing of
/// the store methods may replace.
fn profile(resolved: &Resolved) -> Result<Sink, Error> {
    let routed = resolved.open()?;
    let outside = || MemoryError::configuration("operation is outside the selected profile");
    let history = resolved.profile.history.clone().ok_or_else(outside)?;
    let credential = || {
        resolved
            .credential(&history.source)
            .and_then(|value| value.ok_or_else(|| "source requires a credential".into()))
            .map_err(MemoryError::configuration)
    };
    let binding = resolved.bindings.get(&history.source).ok_or_else(outside)?;
    let source = match &binding.endpoint {
        Endpoint::File { root } => Sink::File(FileStore::open(root)?),
        Endpoint::Http { url, .. } => Sink::Remote(Box::new(RemoteStore::profile_connect(
            url,
            &credential()?,
            resolved.machine_credential(),
        )?)),
        Endpoint::Postgres { schema, .. } => {
            Sink::Postgres(Box::new(PostgresStore::open(&credential()?, schema)?))
        }
    };
    let capturing = resolved.profile.capture.mode != CaptureMode::Off;
    let admits = move |id: &str| {
        let listed = history.conversations.iter().any(|known| known == id);
        let prefixed = history.prefix.as_ref().is_some_and(|p| id.starts_with(p));
        capturing && (prefixed || listed)
    };
    Ok(Sink::Profile {
        routed,
        history: Box::new(source),
        admits: Box::new(admits),
    })
}
