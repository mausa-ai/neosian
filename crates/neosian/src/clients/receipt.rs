//! `neosian-registrations.json`: which entries under `neosian` this
//! installation wrote, and the state of the rename (DESIGN §40.51 item 1).
//! An entry under `neosian` is ours only when the receipt names it and it is
//! unchanged or still a neosian stdio registration; a name is not ownership.

use super::{context::Context, files, naming::NEW};
use crate::{store::time::Stamp, version::VERSION};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

mod owner;

pub use owner::owns_in;
#[cfg(test)]
pub use owner::recognized;
use owner::{members, rebuilt, same_file};

pub const FILE: &str = "neosian-registrations.json";

/// The receipt beside the client's own configuration.
pub fn path(client: &str, context: &Context) -> PathBuf {
    context.directory(client).join(FILE)
}

/// A UTC time at whole seconds, `2026-11-21T12:00:00Z`.
pub fn now() -> String {
    let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH);
    let seconds = since.map_or(0, |since| since.as_secs() as i64);
    Stamp::from_micros(seconds * 1_000_000).z()
}

/// Where a client's receipt is, and where the rename runs that can rebuild a
/// missing one are (DESIGN §40.51 second amendment, R9).
pub struct Book {
    pub file: PathBuf,
    pub migrations: PathBuf,
}

impl Book {
    pub fn of(client: &str, context: &Context) -> Self {
        Self {
            file: path(client, context),
            migrations: super::rename::backup::migrations(context),
        }
    }

    /// The receipt in the client directory `dir`.
    pub fn at(dir: &Path, migrations: &Path) -> Self {
        Self {
            file: dir.join(FILE),
            migrations: migrations.to_path_buf(),
        }
    }
}

/// The receipt; a missing one is rebuilt from the newest completed rename run
/// for the entries it wrote; `Err` when it exists and does not parse, in
/// which case nothing under `neosian` is ours and the receipt is not rewritten.
pub fn load(client: &str, context: &Context) -> Result<Value, String> {
    load_at(&Book::of(client, context))
}

fn load_at(book: &Book) -> Result<Value, String> {
    if !book.file.exists() {
        return Ok(json!({"format": 1, "entries": rebuilt(book)}));
    }
    let parsed = files::read(&book.file).filter(|r| r["format"] == 1 && r["entries"].is_array());
    parsed.ok_or_else(|| {
        format!(
            "{} is not a receipt this release reads",
            book.file.display()
        )
    })
}

/// SHA-256 over the entry with object keys sorted at every level.
pub fn digest(entry: &Value) -> String {
    fn canonical(value: &Value) -> Value {
        match value {
            Value::Object(members) => {
                let mut sorted: Vec<_> = members.iter().collect();
                sorted.sort_by(|a, b| a.0.cmp(b.0));
                Value::Object(
                    sorted
                        .into_iter()
                        .map(|(k, v)| (k.clone(), canonical(v)))
                        .collect(),
                )
            }
            Value::Array(items) => Value::Array(items.iter().map(canonical).collect()),
            other => other.clone(),
        }
    }
    let text = serde_json::to_string(&canonical(entry)).expect("JSON value");
    sha256(text.as_bytes())
}

/// SHA-256 as lower-case hex.
pub fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn save(client: &str, context: &Context, receipt: &Value) -> Result<(), files::Failure> {
    files::write(&path(client, context), receipt, false)
}

/// Record (or replace) the entry under `neosian` at `pointer` as written.
pub fn record(
    client: &str,
    context: &Context,
    file: &Path,
    pointer: &str,
    entry: &Value,
) -> Result<(), files::Failure> {
    put(client, context, file, pointer, entry, "done")
}

/// Record the intent to write `entry` under `neosian` at `pointer`, before
/// any CLI call or write (R4); `record` completes it after the read-back.
pub fn pending(
    client: &str,
    context: &Context,
    file: &Path,
    pointer: &str,
    entry: &Value,
) -> Result<(), files::Failure> {
    put(client, context, file, pointer, entry, "pending")
}

fn put(
    client: &str,
    context: &Context,
    file: &Path,
    pointer: &str,
    entry: &Value,
    state: &str,
) -> Result<(), files::Failure> {
    let mut receipt = load(client, context).map_err(|e| files::Failure::new(e, files::FIX))?;
    // A repeat setup that finds the same entry recorded as written leaves
    // the receipt's bytes as they are (setup's repeat-run contract).
    let unchanged = receipt["entries"].as_array().is_some_and(|list| {
        list.iter().any(|e| {
            e["pointer"] == pointer
                && e["file"].as_str().is_some_and(|f| same_file(f, file))
                && e["digest"] == digest(entry).as_str()
                && e["state"] == "done"
                && e["release"] == VERSION
        })
    });
    if state == "done" && unchanged {
        return Ok(());
    }
    forget_in(&mut receipt, &[(file.to_path_buf(), pointer.to_owned())]);
    let mut record = json!({
        "file": file, "pointer": pointer, "key": NEW, "digest": digest(entry),
        "state": state, "release": VERSION, "written_at": now()});
    if state == "pending" {
        record["members"] = members(entry);
    }
    receipt["entries"]
        .as_array_mut()
        .expect("entries")
        .push(record);
    save(client, context, &receipt)
}

/// Drop the named entries; whether any was there.
fn forget_in(receipt: &mut Value, entries: &[(PathBuf, String)]) -> bool {
    let Some(list) = receipt["entries"].as_array_mut() else {
        return false;
    };
    let before = list.len();
    list.retain(|e| {
        !entries.iter().any(|(file, pointer)| {
            e["pointer"] == pointer.as_str()
                && e["file"].as_str().is_some_and(|f| same_file(f, file))
        })
    });
    list.len() != before
}

/// Drop the named entries (an undo), and set the migration's state.
pub fn settle(
    client: &str,
    context: &Context,
    entries: &[(PathBuf, String)],
    migration: Option<(&str, &str)>,
) -> Result<(), files::Failure> {
    let mut receipt = load(client, context).map_err(|e| files::Failure::new(e, files::FIX))?;
    let forgotten = forget_in(&mut receipt, entries);
    if let Some((run, state)) = migration {
        receipt["migration"] = json!({"run": run, "state": state, "at": now()});
    } else if !forgotten {
        return Ok(());
    }
    save(client, context, &receipt)
}

/// The migration's state, `held` after an undo.
pub fn state(client: &str, context: &Context) -> Option<String> {
    let receipt = load(client, context).ok()?;
    receipt["migration"]["state"].as_str().map(str::to_owned)
}

pub fn held(client: &str, context: &Context) -> bool {
    state(client, context).as_deref() == Some("held")
}

/// Clear a hold so the next refresh renames again (`setup --refresh --rename`).
pub fn release(client: &str, context: &Context) -> Result<bool, files::Failure> {
    if !held(client, context) {
        return Ok(false);
    }
    let mut receipt = load(client, context).map_err(|e| files::Failure::new(e, files::FIX))?;
    receipt["migration"]["state"] = json!("released");
    receipt["migration"]["at"] = json!(now());
    save(client, context, &receipt).map(|()| true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_digest_ignores_member_order_and_recognition_is_narrow() {
        let one = json!({"command": "neosian", "args": ["mcp"], "env": {"A": "1", "B": "2"}});
        let two = json!({"env": {"B": "2", "A": "1"}, "args": ["mcp"], "command": "neosian"});
        assert_eq!(digest(&one), digest(&two));
        assert_ne!(
            digest(&one),
            digest(&json!({"command": "neosian", "args": ["mcp"]}))
        );
        for entry in [
            json!({"command": "/usr/local/bin/neosian", "args": ["mcp", "--root", "/x"]}),
            json!({"command": r"C:\Users\me\.neosian\bin\neosian.exe", "args": ["mcp"]}),
            json!({"type": "stdio", "command": "neosian", "args": ["mcp"]}),
            json!({"type": "local", "command": ["/opt/neosian", "mcp"]}),
        ] {
            assert!(recognized(&entry), "{entry}");
        }
        for entry in [
            json!({"type": "http", "url": "https://memory.example/mcp"}),
            json!({"command": "neosian", "args": ["record"]}),
            json!({"command": "python3", "args": ["-m", "neosian_cloud_bridge"]}),
            json!({"command": "/opt/neosian-tools/run", "args": ["mcp"]}),
            json!({"type": "sse", "command": "neosian", "args": ["mcp"]}),
        ] {
            assert!(!recognized(&entry), "{entry}");
        }
    }
}
