//! Scoped knowledge workflows over ordinary, versioned memory documents.

pub mod cli;
pub(crate) mod govern;
pub(crate) mod governance;
#[cfg(test)]
mod governance_tests;
pub(crate) mod input;
pub(crate) mod json;
pub(crate) mod model;
pub(crate) mod plan;
pub(crate) mod read;
pub(crate) mod rules;
#[cfg(test)]
mod tests;

use serde_json::Value;

use crate::memory::dispatch::Memory;
use crate::memory::mounts::{Mount, resolve};
use crate::store::{Document, error::Error};
use model::{DEFINITION, invalid, ordinary};

fn target<'a>(memory: &'a Memory, path: &'a str) -> Result<(&'a Mount, &'a str), Error> {
    if !path.starts_with('/') || path.ends_with('/') || path.starts_with("//") {
        return Err(invalid(
            "knowledge paths must be absolute mounted paths without a trailing slash",
        ));
    }
    let (mount, document) = resolve(&memory.mounts, path)?;
    ordinary(document)?;
    Ok((mount, document))
}

fn snapshot(scope: &str, path: &str, doc: Option<&Document>) -> Result<Option<Value>, Error> {
    doc.map(|doc| crate::wire::encode::document(scope, path, doc).map_err(Error::from))
        .transpose()
}

fn definition(
    memory: &Memory,
    mount: &Mount,
    prefix: &str,
) -> Result<Option<rules::Definition>, Error> {
    match memory
        .store
        .read(&mount.scope, &format!("{prefix}/{DEFINITION}"))?
    {
        Some(doc) if doc.envelope.redacted => Err(invalid("collection definition is redacted")),
        Some(doc) => rules::Definition::parse(&doc.content).map(Some),
        None => Ok(None),
    }
}

fn text_error(error: Error) -> String {
    crate::conversation::Error::from(error).text()
}
