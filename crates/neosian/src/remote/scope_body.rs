//! The body `store/restore_scope` posts, extracted from the `State` door
//! (`state.rs`) unchanged so a login import measures a scope unit by the
//! same value (the L1b slice of the L1 plan).

use crate::store::Restored;
use crate::store::error::Error;
use crate::store::journal::{Act, Row};
use crate::wire::encode;
use serde_json::{Value, json};

/// The body `store/restore_scope` posts, as the value `send_json` writes.
/// The login import measures a scope unit by these bytes (the L1b slice of
/// the L1 plan), so the two never differ.
pub(crate) fn restore_scope_body(
    scope: &str,
    documents: &[Restored],
    versions: &[Row],
    redactions: &[Act],
) -> Result<Value, Error> {
    let documents = documents
        .iter()
        .map(|doc| encode::document_parts(scope, &doc.path, &doc.content, &doc.envelope))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({
        "scope": scope,
        "documents": documents,
        "versions": versions.iter().map(encode::version).collect::<Vec<_>>(),
        "redactions": redactions.iter().map(encode::redaction).collect::<Vec<_>>(),
    }))
}
