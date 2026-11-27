//! One conversation's erasure trail as a transfer reads it (DESIGN §40.54
//! item 22): every act, oldest first in the order the source stored them,
//! a page at a time. A FileStore and PostgreSQL are read through their own
//! page readers, never through `State`, whose seam is frozen. A server is
//! asked the released audit read, which answers at most 500 acts, newest
//! first by time. An answer short of that is the whole trail and is used in
//! time order. A full answer cannot show that no older act exists, so the
//! export refuses and names the local source to export from instead.

use crate::conversation::{Error, Redaction};
use crate::postgres::PostgresStore;
use crate::remote::RemoteStore;
use crate::state::State;
use crate::state::records::Source;
use crate::store::FileStore;
use crate::store::erasure::TrailAt;

/// Acts a transfer reads at a time from a local store.
const PAGE: usize = 500;
/// The most acts the released audit read answers (`conversation/turn_redactions`).
const CEILING: usize = 500;

/// Where a transfer reads trails.
pub(super) enum Trail<'a> {
    File(&'a FileStore),
    Postgres(&'a PostgresStore),
    Remote(&'a RemoteStore),
}

type Visit<'v> = dyn FnMut(Vec<Redaction>) -> Result<(), Error> + 'v;

impl<'a> Trail<'a> {
    pub(super) fn of(source: &'a Source) -> Self {
        match source {
            Source::File(store) => Self::File(store),
            Source::Postgres(store) => Self::Postgres(store),
            Source::Remote(store) => Self::Remote(store),
        }
    }

    /// Refuses before the first unit is written when a server's trail may
    /// be longer than its answer. A local trail is always read whole.
    pub(super) fn preflight(&self, id: &str) -> Result<(), Error> {
        if let Self::Remote(store) = self {
            legacy(store, id)?;
        }
        Ok(())
    }

    /// Hands every act of `id`'s trail to `visit`, a page at a time, oldest
    /// first in the order the source stored them.
    pub(super) fn each_page(&self, id: &str, visit: &mut Visit<'_>) -> Result<(), Error> {
        match self {
            Self::File(store) => paged(TrailAt::default(), visit, |at| {
                store.trail_page(id, at, PAGE)
            }),
            Self::Postgres(store) => paged(0, visit, |after| store.trail_page(id, after, PAGE)),
            Self::Remote(store) => visit(legacy(store, id)?),
        }
    }
}

/// Pages from `at` until a page says none follows.
fn paged<K>(
    mut at: K,
    visit: &mut Visit<'_>,
    mut page: impl FnMut(K) -> Result<(Vec<Redaction>, Option<K>), Error>,
) -> Result<(), Error> {
    loop {
        let (acts, next) = page(at)?;
        visit(acts)?;
        match next {
            Some(next) => at = next,
            None => return Ok(()),
        }
    }
}

/// A server's trail through the released audit read, oldest first, when
/// its answer is short of the read's ceiling. A server without erasure has
/// no trail, as before.
fn legacy(store: &RemoteStore, id: &str) -> Result<Vec<Redaction>, Error> {
    let ids = [id.to_string()];
    let mut acts = match store.turn_redactions(Some(&ids), None, CEILING as i64) {
        Ok(acts) => acts,
        Err(Error::Memory(error)) if error.code == "agent_configuration_error" => Vec::new(),
        Err(error) => return Err(error),
    };
    if acts.len() >= CEILING {
        return Err(Error::Value(format!(
            "conversation {id} has at least {CEILING} erasure acts on the server, the most its \
             audit read answers, so its trail may be longer than the server can show and an \
             archive could miss older acts. Export from the store itself instead: --root DIR \
             for a FileStore, or NEOSIAN_POSTGRES_DSN for PostgreSQL"
        )));
    }
    acts.reverse();
    Ok(acts)
}
