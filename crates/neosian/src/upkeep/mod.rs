//! Reviewed, bounded upkeep over the existing state contract.

mod apply;
pub mod cli;
pub(crate) mod model;
mod prepare;
mod scan;
#[cfg(test)]
mod tests;

pub use apply::{apply, inspect};
pub use prepare::prepare;
pub use scan::scan;

use serde_json::Value;

use crate::knowledge::model::{DEFINITION, MAX_BODY, digest, invalid, ordinary};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::{Mount, resolve};
use crate::store::{Document, error::Error};
use model::Guard;

fn address<'a>(
    memory: &'a Memory,
    path: &'a str,
    definition: bool,
) -> Result<(&'a Mount, &'a str), Error> {
    if !path.starts_with('/') || path.starts_with("//") || path.ends_with('/') {
        return Err(invalid("upkeep requires canonical /mount/document paths"));
    }
    let (mount, name) = resolve(&memory.mounts, path)?;
    if definition && let Some(parent) = name.strip_suffix(&format!("/{DEFINITION}")) {
        ordinary(parent)?;
    } else {
        ordinary(name)?;
    }
    Ok((mount, name))
}

fn source(memory: &Memory, scope: &str) -> Option<String> {
    memory.store.profile().and_then(|p| {
        p.mounts
            .iter()
            .find(|m| m.scope == scope)
            .map(|m| m.source.clone())
    })
}

fn workspace(memory: &Memory) -> Option<String> {
    memory.store.profile().map(|p| p.workspace_id.clone())
}

fn fingerprint(scope: &str, path: &str, doc: Option<&Document>) -> Result<String, Error> {
    let mut value = match doc {
        Some(doc) => crate::wire::encode::document(scope, path, doc)?,
        None => Value::Null,
    };
    value.sort_all_objects();
    Ok(digest(
        serde_json::to_vec(&value).expect("snapshot serializes"),
    ))
}

fn observe(memory: &Memory, path: &str) -> Result<(Guard, Option<Document>), Error> {
    let (mount, name) = address(memory, path, true)?;
    let doc = memory.store.read(&mount.scope, name)?;
    if doc.as_ref().is_some_and(|d| d.content.len() > MAX_BODY) {
        return Err(invalid("upkeep document exceeds 1 MiB"));
    }
    let guard = Guard {
        path: path.into(),
        scope: mount.scope.clone(),
        source: source(memory, &mount.scope),
        version: doc.as_ref().map(|d| d.envelope.version),
        sha256: fingerprint(&mount.scope, name, doc.as_ref())?,
    };
    Ok((guard, doc))
}

fn current(doc: Option<Document>) -> Result<Document, Error> {
    doc.filter(|d| !d.envelope.redacted)
        .ok_or_else(|| invalid("upkeep source is missing or redacted"))
}

fn same(a: &Guard, b: &Guard) -> bool {
    a.scope == b.scope && a.source == b.source && a.version == b.version && a.sha256 == b.sha256
}

fn text_error(error: Error) -> String {
    crate::conversation::Error::from(error).text()
}
