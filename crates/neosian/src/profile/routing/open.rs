//! A profile's sources opened from their local bindings: every credential
//! is read before any store opens, and each store is opened once and kept
//! as its concrete kind, so the history source's record methods are the
//! same store the routed methods use.

use std::collections::BTreeMap;
use std::sync::Arc;

use super::records::HistoryRecords;
use super::{Routed, denied};
use crate::postgres::PostgresStore;
use crate::profile::files::Endpoint;
use crate::profile::resolve::Resolved;
use crate::remote::RemoteStore;
use crate::state::State;
use crate::state::records::Source;
use crate::store::FileStore;
use crate::store::error::{Error, MemoryError};

enum Opening {
    File(std::path::PathBuf),
    Remote { url: String, token: String },
    Postgres { dsn: String, schema: String },
}

impl Resolved {
    pub fn open(&self) -> Result<Arc<dyn State>, Error> {
        Ok(self.routed()?)
    }

    /// The profile opened for its history records: the routed store every
    /// other method goes through, and the record methods of its history
    /// source under the profile's authority. No history, no records.
    pub fn open_history(&self) -> Result<HistoryRecords, Error> {
        if self.profile.history.is_none() {
            return Err(denied());
        }
        Ok(HistoryRecords::new(self.routed()?))
    }

    fn routed(&self) -> Result<Arc<Routed>, Error> {
        self.check().map_err(MemoryError::configuration)?;
        let mut openings = BTreeMap::new();
        for id in &self.profile.sources {
            let credential = || {
                self.credential(id)
                    .and_then(|v| v.ok_or_else(|| "source requires a credential".into()))
                    .map_err(MemoryError::configuration)
            };
            let opening = match &self.bindings[id].endpoint {
                Endpoint::File { root } => Opening::File(root.clone()),
                Endpoint::Http { url, .. } => Opening::Remote {
                    url: url.clone(),
                    token: credential()?,
                },
                Endpoint::Postgres { schema, .. } => Opening::Postgres {
                    dsn: credential()?,
                    schema: schema.clone(),
                },
            };
            openings.insert(id.clone(), opening);
        }
        let mut sources = BTreeMap::new();
        for (id, opening) in openings {
            let source = match opening {
                Opening::File(root) => Source::File(FileStore::open(&root)?),
                Opening::Remote { url, token } => Source::Remote(Box::new(
                    RemoteStore::profile_connect(&url, &token, self.machine_credential())?,
                )),
                Opening::Postgres { dsn, schema } => {
                    Source::Postgres(Box::new(PostgresStore::open(&dsn, &schema)?))
                }
            };
            sources.insert(id, source);
        }
        Ok(Arc::new(Routed {
            profile: self.profile.clone(),
            sources,
        }))
    }
}
