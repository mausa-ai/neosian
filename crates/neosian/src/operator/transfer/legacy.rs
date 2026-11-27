//! The whole-body units of a transfer (DESIGN §26): a memory scope, and a
//! conversation that goes whole, through `restore_scope` and
//! `restore_conversation` as before. On an import into a server that keeps
//! records, a rerun after an interruption may meet such a unit that an
//! earlier run already restored. It is passed over only when an exact
//! read-back shows the target holding the archive's unit and nothing else;
//! otherwise the import refuses it as occupied (the T1e2 plan's ruling on
//! mixed imports). Values are compared in their wire form, which is what
//! the target was sent and answers.

use serde_json::Value;

use super::Unit;
use super::trail::Trail;
use crate::conversation::Error;
use crate::state::State;
use crate::store::Restored;
use crate::store::error::MemoryError;
use crate::store::journal::{Act, Row};
use crate::wire::encode;

/// A scope is occupied when it holds a document, a version or an erasure.
pub(super) fn scope_occupied(target: &dyn State, scope: &str) -> Result<bool, Error> {
    Ok(!target.list_documents(scope, "")?.is_empty()
        || !target.history(scope, None, Some(1))?.is_empty()
        || !target.redactions(scope, None, Some(1))?.is_empty())
}

/// A scope's live documents, versions by path and number, and erasure
/// acts oldest first, as an archive carries them.
struct Scope {
    documents: Vec<Restored>,
    versions: Vec<Row>,
    redactions: Vec<Act>,
}

fn gather(source: &dyn State, scope: &str) -> Result<Scope, Error> {
    let mut documents = Vec::new();
    for entry in source.list_documents(scope, "")? {
        if let Some(document) = source.read(scope, &entry.path)? {
            documents.push(Restored {
                path: entry.path,
                content: document.content,
                envelope: document.envelope,
            });
        }
    }
    let mut versions = source.history(scope, None, None)?;
    versions.sort_by(|a, b| (&a.path, a.version).cmp(&(&b.path, b.version)));
    let mut redactions = source.redactions(scope, None, None)?;
    redactions.reverse();
    Ok(Scope {
        documents,
        versions,
        redactions,
    })
}

impl Scope {
    fn unit(&self, scope: &str) -> Unit {
        Unit {
            kind: "scope",
            name: scope.to_string(),
            documents: self.documents.len(),
            versions: self.versions.len(),
            redactions: self.redactions.len(),
            ..Unit::default()
        }
    }

    /// Every value in its wire form, sorted, one list per kind.
    fn wire(&self, scope: &str) -> Result<[Vec<String>; 3], Error> {
        let documents = self.documents.iter().map(|document| {
            encode::document_parts(scope, &document.path, &document.content, &document.envelope)
        });
        let documents = documents.collect::<Result<Vec<Value>, MemoryError>>()?;
        let versions = self.versions.iter().map(encode::version);
        let redactions = self.redactions.iter().map(encode::redaction);
        Ok([
            texts(documents),
            texts(versions.collect()),
            texts(redactions.collect()),
        ])
    }
}

/// Values as sorted text, so two lists compare as multisets.
fn texts(values: Vec<Value>) -> Vec<String> {
    let mut texts: Vec<String> = values.iter().map(Value::to_string).collect();
    texts.sort();
    texts
}

/// The most acts the released audit read answers.
const AUDIT: usize = 500;

/// Restores one scope whole and answers its unit.
pub(super) fn restore_scope(
    source: &dyn State,
    target: &dyn State,
    scope: &str,
) -> Result<Unit, Error> {
    let gathered = gather(source, scope)?;
    let unit = gathered.unit(scope);
    target.restore_scope(
        scope,
        &gathered.documents,
        gathered.versions,
        &gathered.redactions,
    )?;
    Ok(unit)
}

/// The unit of an occupied scope when the target holds exactly the
/// archive's documents, versions and erasure acts.
pub(super) fn same_scope(
    source: &dyn State,
    target: &dyn State,
    scope: &str,
) -> Result<Option<Unit>, Error> {
    let archived = gather(source, scope)?;
    let held = gather(target, scope)?;
    let same = archived.wire(scope)? == held.wire(scope)?;
    Ok(same.then(|| archived.unit(scope)))
}

/// Restores one conversation whole and answers its unit. The restore takes
/// one body, so the trail's pages are gathered here; a target keeps the
/// acts it already holds first.
pub(super) fn restore_conversation(
    source: &dyn State,
    target: &dyn State,
    trail: &Trail<'_>,
    id: &str,
) -> Result<Unit, Error> {
    let mut redactions = Vec::new();
    trail.each_page(id, &mut |page| {
        redactions.extend(page);
        Ok(())
    })?;
    let turns = source.read_turns(id, 0, None)?;
    let projections = source.read_projections(id, 0, None)?;
    target.restore_conversation(id, &turns, &projections, &redactions)?;
    Ok(Unit {
        kind: "conversation",
        name: id.to_string(),
        turns: turns.len(),
        projections: projections.len(),
        redactions: redactions.len(),
        ..Unit::default()
    })
}

/// Whether the target keeps an erasure act of the conversation; a server
/// without erasure keeps none.
pub(super) fn trail_held(target: &dyn State, id: &str) -> Result<bool, Error> {
    let ids = [id.to_string()];
    match target.turn_redactions(Some(&ids), None, 1) {
        Ok(acts) => Ok(!acts.is_empty()),
        Err(Error::Memory(error)) if error.code == "agent_configuration_error" => Ok(false),
        Err(error) => Err(error),
    }
}

/// The unit of an occupied conversation that goes whole (one holding no
/// turn in the archive) when the target holds exactly its turns, its
/// projections in order and its erasure acts. A target whose trail its
/// audit read may not show whole is not established as equal.
pub(super) fn same_conversation(
    source: &dyn State,
    target: &dyn State,
    trail: &Trail<'_>,
    id: &str,
) -> Result<Option<Unit>, Error> {
    let ids = [id.to_string()];
    let turns = |state: &dyn State| -> Result<Vec<String>, Error> {
        let turns = state.read_turns(id, 0, None)?;
        Ok(turns
            .iter()
            .map(|turn| encode::turn(turn).to_string())
            .collect())
    };
    let projections = |state: &dyn State| -> Result<Vec<String>, Error> {
        let entries = state.read_projections(id, 0, None)?;
        Ok(entries
            .iter()
            .map(|entry| encode::projection(entry).to_string())
            .collect())
    };
    let mut archived = Vec::new();
    trail.each_page(id, &mut |page| {
        archived.extend(page.iter().map(encode::turn_redaction));
        Ok(())
    })?;
    if archived.len() >= AUDIT {
        return Ok(None);
    }
    // One act more than the archive's shows a trail longer than its own.
    let held = target.turn_redactions(Some(&ids), None, archived.len() as i64 + 1)?;
    let held = texts(held.iter().map(encode::turn_redaction).collect());
    let count = archived.len();
    let (archived_turns, archived_entries) = (turns(source)?, projections(source)?);
    let same = held == texts(archived)
        && turns(target)? == archived_turns
        && projections(target)? == archived_entries;
    Ok(same.then(|| Unit {
        kind: "conversation",
        name: id.to_string(),
        turns: archived_turns.len(),
        projections: archived_entries.len(),
        redactions: count,
        ..Unit::default()
    }))
}
