//! Explicit machine-local native memory capture. No host database or model access.

pub mod cli;
mod config;
pub mod hooks;
mod scan;

use crate::memory::mounts::{Mount, resolve, structural, writable};
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::store::error::Error;
use crate::store::grammar::Actor;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

pub(crate) fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn invalid(message: impl Into<String>) -> Error {
    Error::Value(message.into())
}

fn error_text(error: Error) -> String {
    match error {
        Error::Memory(e) => format!("[{}] {}", e.code, e.message),
        Error::Value(text) => text,
        Error::Io(e) => e.to_string(),
    }
}

fn local(selection: &Selection) -> Result<PathBuf, Error> {
    match selection {
        Selection::File(root) => config::absolute(root),
        _ => Err(invalid(
            "native memory capture requires a local FileStore without a profile; profiles and remote stores are not supported",
        )),
    }
}

fn authority(source: &config::Source, root: &Path, mounts: &[Mount]) -> Result<(), Error> {
    if root != source.store {
        return Err(invalid(
            "native memory registration names a different store",
        ));
    }
    let (mount, path) = resolve(mounts, &source.destination)?;
    if mount.scope != source.scope || path != format!("native-memory/{}", source.name) {
        return Err(invalid(
            "native memory registration no longer matches the mounted scope",
        ));
    }
    writable(mount)?;
    structural(mount)?;
    Ok(())
}

fn sync(
    source: &config::Source,
    root: &Path,
    mounts: &[Mount],
    actor: &Actor,
    write: bool,
) -> Result<Value, Error> {
    // Check mounted authority before reading any source bytes.
    authority(source, root, mounts)?;
    let store = FileStore::open(root)?;
    store.with_native_capture(|capture| {
        let files = scan::read(source)?;
        let mut actions = Vec::new();
        let mut success = true;
        let mut recovery = false;
        for file in files {
            let path = format!("native-memory/{}/{}", source.name, file.name);
            let shown = format!("{}/{}", source.destination, file.name);
            if recovery {
                actions.push(json!({"path":shown,"action":"not_attempted"}));
                continue;
            }
            let content = format!(
                "<!-- neosian-native-memory {} -->\n{}",
                json!({"format":1,"source":source.name,"agent":source.agent,"file":file.name}),
                file.content,
            );
            let result = capture.write(&source.scope, &path, &source.id(), &content, actor, write);
            match result {
                Ok((action, version)) => {
                    actions.push(json!({"path":shown,"action":action,"version":version}))
                }
                Err(error) => {
                    success = false;
                    recovery = capture.recovery_pending();
                    let action = if recovery {
                        "recovery_required"
                    } else {
                        "refused"
                    };
                    actions.push(json!({"path":shown,"action":action,"error":error_text(error)}));
                }
            }
        }
        Ok(
            json!({"source":source.name,"destination":source.destination,"preview":!write,
        "success":success,"actions":actions}),
        )
    })
}
