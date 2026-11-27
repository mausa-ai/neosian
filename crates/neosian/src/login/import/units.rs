//! What a snapshot transfers and what it leaves out (the L1b slice of the
//! L1 plan). Every memory scope the copy lists is a unit, and so is every
//! conversation holding a turn, each measured by the bytes that leave
//! (`operator::transfer::units`). A conversation without turns, holding
//! projections or an erasure trail only, has no route to the host: it is
//! named as an omission with what it holds (the ruling on zero-turn units),
//! never dropped silently. One holding a trail alone is not listed by the
//! store, so the copy's conversation directories are walked for it.
//! Scopes come first, then conversations, each in name order, so two runs
//! over the same snapshot answer the same units in the same order.

use std::fs;

use super::snapshot::Snapshot;
use crate::conversation::Error;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::rows::CONVERSATIONS;
use crate::operator::transfer::units::{self, Measured};

/// The reason a conversation without turns is left out.
pub(crate) const NO_TURNS: &str = "no_turns";

/// A conversation the import leaves out, with what it holds.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Omission {
    pub kind: &'static str,
    pub name: String,
    pub reason: &'static str,
    pub projections: usize,
    pub redactions: usize,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct Inventory {
    pub units: Vec<Measured>,
    pub omitted: Vec<Omission>,
}

impl Inventory {
    /// What the units add up to: the counts the device start shows.
    pub(crate) fn totals(&self) -> (usize, usize, u64) {
        let scopes = self.units.iter().filter(|u| u.kind == "scope").count();
        let bytes = self.units.iter().map(|unit| unit.bytes).sum();
        (scopes, self.units.len() - scopes, bytes)
    }
}

/// Where each body goes: the unit's index, the part's index, its bytes.
pub(crate) type Bodies<'b> = dyn FnMut(usize, usize, &[u8]) -> Result<(), Error> + 'b;

/// Measures every unit of `snapshot`, handing each body to `bodies` once.
pub(crate) fn inventory(snapshot: &Snapshot, bodies: &mut Bodies<'_>) -> Result<Inventory, Error> {
    let store = snapshot.store()?;
    let mut made = Inventory::default();
    for scope in store.scopes()? {
        let (unit, body) = units::scope(&store, &scope)?;
        bodies(made.units.len(), 0, &body)?;
        made.units.push(unit);
    }
    let listed = store.conversations()?;
    for id in &listed {
        if store.last_turn_number(id)? == 0 {
            made.omitted.push(Omission {
                kind: "conversation",
                name: id.clone(),
                reason: NO_TURNS,
                projections: store.read_projections(id, 0, None)?.len(),
                redactions: units::acts(&store, id)?,
            });
            continue;
        }
        let index = made.units.len();
        let mut part = 0;
        let unit = units::conversation(&store, id, &mut |body| {
            part += 1;
            bodies(index, part - 1, body)
        })?;
        made.units.push(unit);
    }
    let directory = snapshot.root().join(CONVERSATIONS);
    let mut trails = Vec::new();
    if directory.is_dir() {
        for entry in fs::read_dir(&directory)? {
            let Ok(name) = entry?.file_name().into_string() else {
                continue;
            };
            if parse_conversation_id(&name).is_err() || listed.contains(&name) {
                continue;
            }
            let redactions = units::acts(&store, &name)?;
            if redactions > 0 {
                trails.push(Omission {
                    kind: "conversation",
                    name,
                    reason: NO_TURNS,
                    projections: 0,
                    redactions,
                });
            }
        }
    }
    made.omitted.extend(trails);
    made.omitted.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(made)
}
