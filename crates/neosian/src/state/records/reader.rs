//! One store opened for the agent doors (DESIGN §40.54 items 15.2 and 19):
//! the state every method goes through and, from the same opened store,
//! its record methods while it keeps records. A FileStore, a PostgresStore
//! and a server are opened once and shared with the state. A profile with
//! history reads its history source's records under the profile's
//! authority (`profile/routing/records.rs`); a profile without history
//! reads none and still serves the doors it serves.

use std::sync::Arc;

use super::{Keeper, Kept, Records};
use crate::conversation::Error;
use crate::limits;
use crate::postgres::PostgresStore;
use crate::profile::HistoryRecords;
use crate::remote::RemoteStore;
use crate::remote::selection::Selection;
use crate::state::State;
use crate::store::FileStore;
use crate::store::error::Error as StoreError;

/// The doors' store: its state, and its records when it keeps them.
pub enum HistoryReader {
    File(Arc<FileStore>),
    Postgres(Arc<PostgresStore>),
    Remote(Arc<RemoteStore>),
    /// A profile with history: its routed store and its history source.
    Profile(HistoryRecords),
    /// A store whose records the doors never read: a profile without
    /// history.
    Whole(Arc<dyn State>),
}

impl HistoryReader {
    /// The store `selection` names, opened once.
    pub fn open(selection: &Selection) -> Result<Self, StoreError> {
        Ok(match selection {
            Selection::File(root) => Self::File(Arc::new(FileStore::open(root)?)),
            Selection::Postgres { dsn, schema } => {
                Self::Postgres(Arc::new(PostgresStore::open(dsn, schema)?))
            }
            Selection::Remote { url, token } => {
                Self::Remote(Arc::new(RemoteStore::connect(url, token)?))
            }
            Selection::Profile(resolved) if resolved.profile.history.is_some() => {
                Self::Profile(resolved.open_history()?)
            }
            Selection::Profile(resolved) => Self::Whole(resolved.open()?),
        })
    }

    /// The state every other method goes through: the same store.
    pub fn state(&self) -> Arc<dyn State> {
        match self {
            Self::File(store) => store.clone(),
            Self::Postgres(store) => store.clone(),
            Self::Remote(store) => store.clone(),
            Self::Profile(history) => history.shared(),
            Self::Whole(state) => state.clone(),
        }
    }

    /// The records side when the store keeps records now; `None` is the
    /// capability's absence, the only reason a door reads a turn whole.
    pub fn records(&self) -> Result<Option<Box<dyn Records + '_>>, Error> {
        fn boxed<'a>(records: impl Records + 'a) -> Box<dyn Records + 'a> {
            Box::new(records)
        }
        Ok(match self {
            Self::File(store) => file(store)?.map(boxed),
            Self::Postgres(store) => postgres(store)?.map(boxed),
            Self::Remote(store) => remote(store).map(boxed),
            Self::Profile(history) => history.records()?.map(boxed),
            Self::Whole(_) => None,
        })
    }
}

/// A FileStore's records after `neosian records enable`; its record
/// settings are read only then.
pub(super) fn file(store: &FileStore) -> Result<Option<Keeper<'_>>, Error> {
    if !store.records_enabled()? {
        return Ok(None);
    }
    let kept = Kept::File(store, limits::Records::load()?);
    Ok(Some(Keeper { kept }))
}

/// A PostgresStore's records with its extension installed.
pub(super) fn postgres(store: &PostgresStore) -> Result<Option<Keeper<'_>>, Error> {
    if !store.supports_records()? {
        return Ok(None);
    }
    let kept = Kept::Postgres(store, limits::Records::load()?);
    Ok(Some(Keeper { kept }))
}

/// A server's records when its handshake declared turn records version 1.
pub(super) fn remote(store: &RemoteStore) -> Option<Keeper<'_>> {
    store.supports_records().then_some(Keeper {
        kept: Kept::Remote(store),
    })
}
