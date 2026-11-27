//! What a login ends of this home's blocked destinations (DESIGN §40.54
//! item 17.5): a login or refresh that applied a changed machine document
//! ends every block, and a successful `neosian login` (not a refresh) ends
//! the credential blocks. A document is changed when the host sent an
//! ETag other than the one this home kept; one that came without an ETag,
//! or with the same one, or a 304, changes nothing. Best effort: a record
//! that cannot be changed is left for `record retry` or the six-hour
//! attempt, and the login answers as it would have.

use std::path::Path;

use serde_json::Value;

use super::Login;
use super::local::{self, State};
use crate::record::landing::destination::unblock;
use crate::record::landing::rules::Block;
use crate::record::landing::slots::directory;

/// After a login or refresh answered `report`, with `prior` the state it
/// started from.
pub fn changed(home: &Path, prior: Option<&State>, report: &Value) {
    if report["updated"] != true {
        return;
    }
    let tag = local::state(home)
        .ok()
        .flatten()
        .and_then(|state| state.etag);
    if tag.is_some() && tag.as_deref() != prior.and_then(|state| state.etag.as_deref()) {
        let _ = unblock(&directory(home), |_| true, false);
    }
}

/// After a login `args` asked for succeeded: its credential blocks end.
pub fn signed_in(home: &Path, args: &Login) {
    if !args.refresh {
        let _ = unblock(
            &directory(home),
            |block| block.reason == Block::Credential.name(),
            false,
        );
    }
}
