//! The record methods of a concrete store (DESIGN §40.54 items 9 to 13),
//! beside `State` and never on it: the trait is a frozen seam, and only a
//! FileStore, a PostgresStore and a server keep records. `Source::keeper`
//! answers `None` while a store keeps no records, so a caller can tell the
//! capability's absence from a refusal. A local store is written under
//! the record limits read with its keeper and its reads are capped by
//! `NEOSIAN_MAX_PAGE_BYTES`, as `neosian serve` caps them; a server checks
//! its own limits and is asked only when its handshake declared turn
//! records version 1. A server's failure keeps its status, `Retry-After`,
//! code and details. A profile reaches these methods only through
//! `profile/routing/records.rs`, under its history authority, and never
//! reaches a restore or the sweep.

use super::State;
use crate::conversation::Error;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::sweep::SweepPage;
use crate::conversation::turn_records::{
    ReadItem, ReadPosition, RecordHit, RecordRead, RecordSearch, Restore, Restored, Write, Written,
};
use crate::limits;
use crate::postgres::PostgresStore;
use crate::remote::RemoteStore;
use crate::remote::records::{Answered, Failure, RecordPage, RecordQuery};
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::store::error::{Error as StoreError, MemoryError};

mod reader;
pub use reader::HistoryReader;

/// Hits a search answers when it names no `limit`, as the route answers.
const HITS: usize = 20;

/// A store selected directly: a FileStore, a PostgresStore or a server.
pub enum Source {
    File(FileStore),
    Postgres(Box<PostgresStore>),
    Remote(Box<RemoteStore>),
}

/// Why a record method did not answer.
#[derive(Debug)]
pub enum Fault {
    /// A local store's refusal or failure, or this side's own refusal.
    Local(Error),
    /// A server's failure as it came.
    Remote(Failure),
}

impl From<Error> for Fault {
    fn from(error: Error) -> Self {
        Self::Local(error)
    }
}

impl From<StoreError> for Fault {
    fn from(error: StoreError) -> Self {
        Self::Local(error.into())
    }
}

impl Fault {
    /// The fault as a command reports it: a local error as it is, a
    /// server's failure in its own words.
    pub fn into_error(self) -> Error {
        match self {
            Self::Local(error) => error,
            Self::Remote(failure) => Error::Value(failure.to_string()),
        }
    }
}

/// The five record methods, as a store or a profile answers them. The
/// lander writes, the archive import restores, `records drafts` sweeps,
/// recall reads and the history search door searches (`HistoryReader`).
pub trait Records {
    /// One part of a draft, a probe, a seal or a discard.
    fn write(&self, id: &str, write: Write) -> Result<Written, Fault>;
    /// A conversation's turns as records, a page at a time: `visit` takes
    /// each page with its continuation and says whether to go on.
    fn read(
        &self,
        id: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    ) -> Result<(), Fault>;
    /// Every hit of one search, its query sent to a server verbatim.
    fn search(&self, query: &RecordQuery) -> Result<Vec<RecordHit>, Fault>;
    /// One part of a restore, a probe, a commit or a discard (operator).
    fn restore(&self, id: &str, restore: Restore) -> Result<Restored, Fault>;
    /// One page of the stale-draft sweep: at most `limit` conversations
    /// after `cursor`, removed when `write` (operator).
    fn sweep(&self, limit: usize, cursor: Option<&str>, write: bool) -> Result<SweepPage, Fault>;
}

/// A store's records while it keeps records now.
pub struct Keeper<'a> {
    kept: Kept<'a>,
}

enum Kept<'a> {
    File(&'a FileStore, limits::Records),
    Postgres(&'a PostgresStore, limits::Records),
    Remote(&'a RemoteStore),
}

impl Source {
    /// The store `selection` names, opened once. A profile is refused: its
    /// records go through its history source under its authority
    /// (`Resolved::open_history`), and never to a restore or the sweep.
    pub fn open(selection: &Selection) -> Result<Self, StoreError> {
        Ok(match selection {
            Selection::File(root) => Self::File(FileStore::open(root)?),
            Selection::Postgres { dsn, schema } => {
                Self::Postgres(Box::new(PostgresStore::open(dsn, schema)?))
            }
            Selection::Remote { url, token } => {
                Self::Remote(Box::new(RemoteStore::connect(url, token)?))
            }
            Selection::Profile(_) => {
                let why = "a profile's records are reached only through its history source";
                return Err(MemoryError::configuration(why).into());
            }
        })
    }

    pub fn state(&self) -> &dyn State {
        match self {
            Self::File(store) => store,
            Self::Postgres(store) => store.as_ref(),
            Self::Remote(store) => store.as_ref(),
        }
    }

    /// The records side when the store keeps records now: a FileStore after
    /// `neosian records enable`, Postgres with its extension, a server whose
    /// handshake declared turn records version 1. A local store's record
    /// settings are read only then.
    pub fn keeper(&self) -> Result<Option<Keeper<'_>>, Error> {
        match self {
            Self::File(store) => reader::file(store),
            Self::Postgres(store) => reader::postgres(store),
            Self::Remote(store) => Ok(reader::remote(store)),
        }
    }

    /// What a store that keeps no records refuses a record method with.
    pub fn absent(&self) -> Error {
        match self {
            Self::File(_) => FileStore::records_absent().into(),
            Self::Postgres(_) => PostgresStore::records_absent().into(),
            Self::Remote(_) => MemoryError::configuration(Failure::Unsupported.to_string()).into(),
        }
    }
}

impl Records for Keeper<'_> {
    fn write(&self, id: &str, write: Write) -> Result<Written, Fault> {
        match &self.kept {
            Kept::File(store, limits) => Ok(store.write_records(id, write, limits)?),
            Kept::Postgres(store, limits) => Ok(store.write_records(id, write, limits)?),
            Kept::Remote(store) => store.write_records(id, &write).map_err(Fault::Remote),
        }
    }

    fn read(
        &self,
        id: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    ) -> Result<(), Fault> {
        match &self.kept {
            Kept::File(store, _) => paged(id, read, visit, &mut |read, items| {
                store.read_records(id, read, items)
            }),
            Kept::Postgres(store, _) => paged(id, read, visit, &mut |read, items| {
                store.read_records(id, read, items)
            }),
            Kept::Remote(store) => {
                // The visitor's own fault stops the read and is answered as
                // it was raised, never as the server's.
                let mut raised = None;
                let read = store.read_records(id, read, &mut |page| {
                    visit(page).or_else(|fault| {
                        raised = Some(fault);
                        Ok(false)
                    })
                });
                match raised {
                    Some(fault) => Err(fault),
                    None => read.map_err(Fault::Remote),
                }
            }
        }
    }

    fn search(&self, query: &RecordQuery) -> Result<Vec<RecordHit>, Fault> {
        // A local store folds the query's terms itself; its selection is
        // the request's.
        let local = |query: &RecordQuery| {
            let (order, kinds) = (query.order.unwrap_or(Order::Newest), query.kinds.clone());
            let (since, before) = (query.since.clone(), query.before.clone());
            let limit = query.limit.unwrap_or(HITS);
            let mut search = RecordSearch::new(&query.query, kinds, order, since, before, limit)
                .map_err(Error::Value)?;
            search.conversations = query.conversations.clone();
            Ok::<_, Error>(search)
        };
        match &self.kept {
            Kept::File(store, _) => Ok(store.search_records(&local(query)?)?),
            Kept::Postgres(store, _) => Ok(store.search_records(&local(query)?)?),
            Kept::Remote(store) => store.search_records(query).map_err(Fault::Remote),
        }
    }

    fn restore(&self, id: &str, restore: Restore) -> Result<Restored, Fault> {
        match &self.kept {
            Kept::File(store, limits) => Ok(store.restore_records(id, restore, limits)?),
            Kept::Postgres(store, limits) => Ok(store.restore_records(id, restore, limits)?),
            Kept::Remote(store) => store.restore_records(id, &restore).map_err(Fault::Remote),
        }
    }

    fn sweep(&self, limit: usize, cursor: Option<&str>, write: bool) -> Result<SweepPage, Fault> {
        match &self.kept {
            Kept::File(store, _) => Ok(store.sweep_records(limit, cursor, write)?),
            Kept::Postgres(store, _) => Ok(store.sweep_records(limit, cursor, write)?),
            Kept::Remote(store) => store
                .sweep_records(limit, cursor, write)
                .map_err(Fault::Remote),
        }
    }
}

type PageOf<'a> = dyn FnMut(
        &RecordRead,
        &mut dyn FnMut(ReadItem) -> Result<(), Error>,
    ) -> Result<Option<ReadPosition>, Error>
    + 'a;

/// A local store's read as a server answers it, a page at a time: each
/// page's headers and records, and the continuation the store's position
/// encodes under the request's fingerprint.
fn paged(
    id: &str,
    read: &RecordRead,
    visit: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    page_of: &mut PageOf<'_>,
) -> Result<(), Fault> {
    let page_bytes = read.page_bytes.min(limits::page_bytes()?);
    let mut read = RecordRead {
        page_bytes,
        ..read.clone()
    };
    loop {
        let (mut turns, mut records) = (Vec::new(), Vec::new());
        let next = page_of(&read, &mut |item| {
            match item {
                ReadItem::Header(header) => turns.push(header),
                ReadItem::Record {
                    turn,
                    record,
                    tail,
                    truncated,
                } => records.push(Answered {
                    turn,
                    record,
                    tail,
                    truncated,
                }),
            }
            Ok(())
        })?;
        let continuation = next.map(|at| at.encode(&read.fingerprint(id)));
        let page = RecordPage {
            turns,
            records,
            continuation: continuation.clone(),
        };
        let more = visit(page)?;
        match continuation {
            Some(token) if more => read.continuation = Some(token),
            _ => return Ok(()),
        }
    }
}
