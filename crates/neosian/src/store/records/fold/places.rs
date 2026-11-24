//! Where a fold finds a name (DESIGN §40.54 item 21, T1b2c4): under the
//! conversations, under the staged restores, or both, each by its exact
//! spelling. A file system that folds case answers for another spelling of
//! a path, so a name is only ever matched against names read from the two
//! directories. They are read once for a run and kept in a `Listing`; under
//! each conversation's lock a directory is read again only when its own
//! time has moved, or when its names were read while that time was too
//! fresh to show a second change. So a run over a store at rest reads each
//! directory once, and a directory another process replaced since the
//! listing is never taken for the one that was listed.

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;
use std::time::{Duration, SystemTime};

use super::super::staging;
use crate::conversation::Error;
use crate::conversation::rows::CONVERSATIONS;
use crate::conversation::search::visit_ids;
use crate::store::FileStore;

/// A directory changed this recently may change again within one tick of
/// its file system's clock, so its time does not yet vouch for its names.
const UNSETTLED: Duration = Duration::from_secs(2);

/// Where a name is stored: a conversation's directory, and the directory
/// of its staged restores, which is all a conversation being restored has.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Places {
    pub stored: bool,
    pub staged: bool,
}

/// One of the two directories: its names, sorted, its time when they
/// were read, and whether that time was old enough then to vouch for them.
#[derive(Default)]
struct Names {
    names: Vec<String>,
    read_at: Option<SystemTime>,
    settled: bool,
}

impl Names {
    /// Whether the names must be read again: the directory's time has
    /// moved, or they were read while it was too fresh to tell.
    fn stale(&self, now: Option<SystemTime>) -> bool {
        now != self.read_at || !self.settled
    }
}

/// The names a run of folds works through.
#[derive(Default)]
pub struct Listing {
    stored: Names,
    staged: Names,
}

impl Listing {
    /// Every name, sorted: a conversation holding only drafts, or only a
    /// staged restore, is here though no other listing shows it.
    pub fn names(&self) -> Vec<String> {
        let all = self.stored.names.iter().chain(&self.staged.names);
        all.cloned().collect::<BTreeSet<_>>().into_iter().collect()
    }

    /// Where exactly this name is stored.
    pub fn places(&self, id: &str) -> Places {
        let listed = |side: &Names| side.names.binary_search_by(|name| name.as_str().cmp(id));
        Places {
            stored: listed(&self.stored).is_ok(),
            staged: listed(&self.staged).is_ok(),
        }
    }
}

/// A directory's time now, and whether it is old enough to vouch for the
/// names read under it: a later change will then show as a later time.
fn stamp(directory: &Path) -> (Option<SystemTime>, bool) {
    let at = fs::metadata(directory)
        .and_then(|meta| meta.modified())
        .ok();
    let settled = at.is_none_or(|at| at.elapsed().is_ok_and(|age| age >= UNSETTLED));
    (at, settled)
}

impl FileStore {
    /// The names a fold can meet: one pass of the conversations and one of
    /// the staged restores, nothing settled.
    pub fn record_places(&self) -> Result<Listing, Error> {
        let _held = self.lock()?;
        let mut listing = Listing::default();
        self.read_places(&mut listing, true)?;
        Ok(listing)
    }

    /// Where the name is stored now, the lock held.
    pub(super) fn places_now(&self, id: &str, listing: &mut Listing) -> Result<Places, Error> {
        self.read_places(listing, false)?;
        Ok(listing.places(id))
    }

    /// Whether a directory of staged restores bears exactly this name now.
    pub(super) fn staged_exactly(&self, id: &str) -> Result<bool, Error> {
        let restores = self.contained(self.root.join(staging::RESTORES), "restores")?;
        let mut found = false;
        visit_ids(&restores, |name| {
            found |= name == id;
            Ok(())
        })?;
        Ok(found)
    }

    fn read_places(&self, listing: &mut Listing, all: bool) -> Result<(), Error> {
        let (read_at, settled) = stamp(&self.root.join(CONVERSATIONS));
        if all || listing.stored.stale(read_at) {
            let names = self.stored_names()?;
            listing.stored = Names {
                names,
                read_at,
                settled,
            };
        }
        let restores = self.contained(self.root.join(staging::RESTORES), "restores")?;
        let (read_at, settled) = stamp(&restores);
        if all || listing.staged.stale(read_at) {
            let mut names = Vec::new();
            visit_ids(&restores, |id| {
                names.push(id.to_string());
                Ok(())
            })?;
            names.sort();
            listing.staged = Names {
                names,
                read_at,
                settled,
            };
        }
        Ok(())
    }
}
