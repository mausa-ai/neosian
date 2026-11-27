//! What a queued turn is written to (DESIGN §40.54 items 7, 9, 15.2 and
//! 17.4): a FileStore, a PostgresStore, a server, or a profile's history
//! source. A store that keeps records now (a FileStore after `neosian
//! records enable`, Postgres with its extension, a server whose handshake
//! declared turn records version 1) takes a turn through its own records
//! methods (`state/records.rs`); any other takes it whole as one
//! `append_turn`, a server's through the status-keeping call of
//! `remote/records/append.rs`. A profile's turn goes to its history source
//! only, through the profile's routing (`profile/routing/records.rs`),
//! which admits each write as it admits `append_turn`. Every answer is an
//! `Answer` (`answer.rs`), so the rules of 17.4 read them alike. A local
//! failure to read or write a file is no answer: it stops the run, and the
//! turn stays queued.

use super::answer::{self, Answer};
use crate::conversation::message::Message;
use crate::conversation::turn_records::Write;
use crate::conversation::{Error, Turn};
use crate::profile::HistoryRecords;
use crate::remote::records::Failure;
use crate::remote::selection::Selection;
use crate::state::State;
use crate::state::records::{self, Fault, Source};
use crate::store::time::Stamp;

pub enum Sink {
    /// A FileStore, a PostgresStore or a server, selected directly.
    Direct(Source),
    /// A profile: its routed store, through which the sessions document is
    /// written as every profile write is, and its history source's record
    /// methods under the profile's authority. The source is opened once.
    Profile(HistoryRecords),
}

/// What became of a turn sent whole.
pub enum Appended {
    Stored(Turn),
    /// The answer that refused or lost it.
    Not(Answer),
}

/// A store's records while it keeps records now, or a profile's history
/// source's under its authority, beside the store whose erasure trail tells
/// a seal that never arrived from an erased turn (`seal.rs`).
pub struct Records<'a> {
    kept: Box<dyn records::Records + 'a>,
    state: &'a dyn State,
}

impl Sink {
    /// The store `selection` names, opened once for a run.
    pub fn open(selection: &Selection) -> Result<Self, Error> {
        Ok(match selection {
            Selection::Profile(resolved) => Self::Profile(resolved.open_history()?),
            direct => Self::Direct(Source::open(direct)?),
        })
    }

    /// The store the sessions document is written to and read from.
    pub fn state(&self) -> &dyn State {
        match self {
            Self::Direct(source) => source.state(),
            Self::Profile(history) => history.state(),
        }
    }

    /// The store a turn of `id` is written to: a profile's history source
    /// once the profile admits `id`, for writing only while capture is on.
    fn target(&self, id: &str, write: bool) -> Result<&Source, Error> {
        match self {
            Self::Direct(source) => Ok(source),
            Self::Profile(history) => history.source(id, write),
        }
    }

    /// Refuses a conversation outside a profile's history, or while its
    /// capture is off, as the profile's routing refuses `append_turn`.
    pub fn admit(&self, id: &str) -> Result<(), Error> {
        self.target(id, true).map(drop)
    }

    /// The records side, when the store keeps records now. A local store's
    /// record settings are read only then.
    pub fn records(&self) -> Result<Option<Records<'_>>, Error> {
        let state = self.state();
        Ok(match self {
            Self::Direct(source) => source.keeper()?.map(|kept| Records {
                kept: Box::new(kept),
                state,
            }),
            Self::Profile(history) => history.records()?.map(|kept| Records {
                kept: Box::new(kept),
                state,
            }),
        })
    }

    /// The turn whole, as one `append_turn` by `actor`. A server is sent
    /// `messages` and they stay, so a 429 can be asked again; a local store
    /// takes them.
    pub fn append(
        &self,
        id: &str,
        messages: &mut Vec<Message>,
        actor: String,
    ) -> Result<Appended, Error> {
        let target = self.target(id, true)?;
        if let Source::Remote(store) = target {
            return Ok(match store.append_turn_kept(id, messages, Some(&actor)) {
                Ok(turn) => Appended::Stored(turn),
                Err(failure) => Appended::Not(answer::remote(failure)),
            });
        }
        let messages = std::mem::take(messages);
        match target.state().append_turn(id, messages, Some(actor)) {
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
        if let Source::Remote(store) = self.target(id, false)?
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
        match self.kept.write(id, write) {
            Ok(written) => Ok(Answer::Written(written)),
            Err(Fault::Local(error)) => answer::local(Err(error)),
            Err(Fault::Remote(failure)) => Ok(answer::remote(failure)),
        }
    }

    /// Whether the store's erasure trail holds no act for conversation
    /// `id`; false when it holds one or cannot be read. An erasure that
    /// matches a turn always leaves an act (wire.md, `redact_turns`).
    pub fn never_erased(&self, id: &str) -> bool {
        let ids = [id.to_string()];
        let acts = self.state.turn_redactions(Some(&ids), None, 1);
        acts.is_ok_and(|acts| acts.is_empty())
    }
}
