//! What recording keeps on this machine and what its destinations
//! answered, read from metadata only (DESIGN §40.54 items 17.4 to 17.8;
//! the T1e2b slice of the T1e2 plan), for `neosian status` and the
//! session-start notices: each destination's record (`destination.rs`),
//! the turns pending and held in the home's spool trees by the destination
//! their states name, whether this process's environment serves those
//! destinations (`serving.rs`), the home's drop counts (`drops.rs`) and
//! the keep limit (`keep.rs`). No turn's content is read, no store is
//! opened and nothing is written; a file that cannot be read counts as
//! none, so a damaged record never stops a hook. A turn whose state cannot
//! be read, or names no destination (one held because its policy changed),
//! is counted apart, with no destination.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use super::aside::{label, said, turns};
use super::destination::{self, Record, key};
use super::drops::{self, Drops};
use super::gate::host;
use super::keep;
use super::serving::{profiled, serving};
use super::slots::directory;
use super::state::{ACTOR_CHANGED, Destination, MISMATCH, POLICY_CHANGED, REFUSED, UNREADABLE};
use crate::remote::selection::Selection;

/// The held reasons a notice may name; any other reads `unknown`.
const REASONS: [&str; 5] = [POLICY_CHANGED, UNREADABLE, REFUSED, MISMATCH, ACTOR_CHANGED];

/// Turns and their bytes.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Count {
    pub turns: u64,
    pub bytes: u64,
}

/// A pending or held turn: its name, `<conversation id>/<n>@<closed_at>`
/// (`-` when its state cannot be read), which a later number or close
/// never repeats; its file's bytes; a held turn's reason keyword.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Turn {
    pub name: String,
    pub bytes: u64,
    pub reason: Option<&'static str>,
}

/// One destination, or the turns with none.
#[derive(Debug, Default)]
pub struct Place {
    /// The record's name (`destination::key`); empty for the turns with
    /// no destination.
    pub key: String,
    /// The host a person knows it by: the record's, else as this process
    /// resolves it, else its label (`aside::label`).
    pub host: String,
    pub destination: Option<Destination>,
    pub record: Option<Record>,
    /// Whether this process's environment serves it; `None` when nothing
    /// is pending for it.
    pub served: Option<bool>,
    /// The destination a profile selected here records to (`status`).
    pub current: bool,
    pub pending: Vec<Turn>,
    pub held: Vec<Turn>,
}

impl Place {
    pub fn count(turns: &[Turn]) -> Count {
        Count {
            turns: turns.len() as u64,
            bytes: turns.iter().map(|turn| turn.bytes).sum(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.pending.is_empty() && self.held.is_empty()
    }
}

pub struct Snapshot {
    /// `[record] keep_bytes`, or `None` when its value is refused.
    pub limit: Option<u64>,
    /// The destinations, by host and then key.
    pub places: Vec<Place>,
    /// The turns with no destination.
    pub loose: Place,
    pub drops: Option<Drops>,
}

impl Snapshot {
    /// Whether nothing is kept, recorded or counted.
    pub fn is_empty(&self) -> bool {
        self.places.is_empty() && self.loose.is_empty() && self.drops.is_none()
    }

    /// The names of every turn held (`held`) or pending in the trees read.
    pub fn names(&self, held: bool) -> BTreeSet<&str> {
        let places = self.places.iter().chain([&self.loose]);
        let turns = places.flat_map(|place| if held { &place.held } else { &place.pending });
        turns.map(|turn| turn.name.as_str()).collect()
    }
}

/// The destination records under `landing`, by key: every `<key>.json`
/// whose key is a record's (`keys.rs`) and whose record can be read.
fn records(landing: &Path) -> Vec<(String, Record)> {
    let keys = super::keys::keys(landing).unwrap_or_default();
    let read = |key: String| destination::read(landing, &key).map(|record| (key, record));
    keys.into_iter().filter_map(read).collect()
}

/// A held turn's reason keyword, `unknown` for one not in `REASONS` or a
/// reason file that cannot be read.
fn reason(directory: &Path, n: u64) -> &'static str {
    let said = said(directory, n).map(|said| said.reason);
    REASONS
        .into_iter()
        .find(|known| said.as_deref() == Some(*known))
        .unwrap_or("unknown")
}

/// How a destination with no record is named: a profile's host as this
/// home resolves it, else its label.
fn named(destination: &Destination, home: &Path) -> String {
    if let Destination::Profile { name, project, .. } = destination
        && let Some((resolved, _)) = profiled(name, project, home)
    {
        return host(&Selection::Profile(Box::new(resolved)));
    }
    label(destination)
}

/// What the home at `home` keeps in `trees` and what its destinations
/// answered, with `current`, the destination a profile selected here
/// records to and its host, among them. `url` is a direct hook's URL when
/// its descriptor cannot carry it (`start::handed`).
pub fn take(
    home: &Path,
    trees: &[PathBuf],
    url: Option<&str>,
    current: Option<(Destination, String)>,
) -> Snapshot {
    let landing = directory(home);
    let mut places: BTreeMap<String, Place> = BTreeMap::new();
    for (key, record) in records(&landing) {
        let host = record.host.clone();
        let record = Some(record);
        places.insert(
            key.clone(),
            Place {
                key,
                host,
                record,
                ..Place::default()
            },
        );
    }
    let mut loose = Place::default();
    let mut states = BTreeMap::new();
    for tree in trees {
        for queue in ["pending", "held"] {
            for (entry, state) in turns(tree, queue).unwrap_or_default() {
                let closed = entry.closed_at.as_deref().unwrap_or("-");
                let directory = tree.join(queue).join(&entry.id);
                let turn = Turn {
                    name: format!("{}/{}@{closed}", entry.id, entry.n),
                    bytes: entry.bytes,
                    reason: (queue == "held").then(|| reason(&directory, entry.n)),
                };
                let destination = state.as_ref().and_then(|s| s.destination.clone());
                let place = match destination {
                    None => &mut loose,
                    Some(destination) => {
                        let key = key(&destination);
                        if queue == "pending"
                            && let Some(state) = state
                        {
                            states.entry(key.clone()).or_insert(state);
                        }
                        let place = places.entry(key.clone()).or_insert_with(|| Place {
                            key,
                            ..Place::default()
                        });
                        place.destination.get_or_insert(destination);
                        place
                    }
                };
                match queue {
                    "pending" => place.pending.push(turn),
                    _ => place.held.push(turn),
                }
            }
        }
    }
    if let Some((destination, host)) = current {
        let key = key(&destination);
        let place = places.entry(key.clone()).or_insert_with(|| Place {
            key,
            host,
            ..Place::default()
        });
        place.destination.get_or_insert(destination);
        place.current = true;
    }
    for place in places.values_mut() {
        let found = match (&place.destination, states.get(&place.key)) {
            (Some(destination), Some(state)) => Some(serving(destination, state, home, url)),
            _ => None,
        };
        place.served = found.as_ref().map(Option::is_some);
        if place.host.is_empty() {
            place.host = match (found.flatten(), &place.destination) {
                (Some(serving), _) => host(serving.selection()),
                (None, Some(destination)) => named(destination, home),
                (None, None) => place.key.clone(),
            };
        }
    }
    let mut places: Vec<Place> = places.into_values().collect();
    places.sort_by(|a, b| (&a.host, &a.key).cmp(&(&b.host, &b.key)));
    Snapshot {
        limit: keep::limit(home).ok(),
        places,
        loose,
        drops: drops::read(&landing),
    }
}
