//! The policy a profile turn is recorded under (DESIGN §40.54 item 17.2,
//! #447): a digest of what decides where the turn may go and what it may
//! hold. A manual profile binds its definition, local bindings and the
//! SHA-256 of each referenced credential value; a managed one binds host,
//! account, workspace, history destination and capture policy, never a
//! credential. A turn lands only while the current fingerprint is the one
//! it was closed with.

use crate::conversation::Error;
use crate::profile::model::CaptureMode;
use crate::profile::resolve::Resolved;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::io;

/// Lowercase hex of the SHA-256 of `value`.
pub fn digest(value: &[u8]) -> String {
    Sha256::digest(value)
        .iter()
        .flat_map(|byte| {
            const HEX: &[u8; 16] = b"0123456789abcdef";
            [
                HEX[(byte >> 4) as usize] as char,
                HEX[(byte & 15) as usize] as char,
            ]
        })
        .collect()
}

pub fn fingerprint(selected: &Resolved) -> Result<String, Error> {
    let profile = &selected.profile;
    let value = if let Some(login) = &selected.login {
        login
            .bound(&selected.home, &selected.name)
            .map_err(Error::Value)?;
        let destination = profile.history.as_ref().map(|h| {
            (
                &h.source,
                &h.scope,
                &h.prefix,
                selected.bindings.get(&h.source),
            )
        });
        json!({"host":login.host, "account":login.account.id, "workspace":profile.workspace_id,
               "destination":destination, "policy":profile.capture})
    } else {
        let mut credentials = serde_json::Map::new();
        if profile.capture.mode != CaptureMode::Off {
            for source in &profile.sources {
                if let Some(name) = selected.bindings[source].credential() {
                    let secret = selected
                        .credential(source)
                        .map_err(Error::Value)?
                        .expect("binding with credential");
                    credentials.insert(name.into(), json!(digest(secret.as_bytes())));
                }
            }
        }
        json!({"profile":profile,"bindings":selected.bindings,"credentials":credentials})
    };
    Ok(digest(
        &serde_json::to_vec(&value).map_err(io::Error::other)?,
    ))
}
