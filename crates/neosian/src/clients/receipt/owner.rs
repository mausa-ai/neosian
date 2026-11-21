//! Whether an entry under `neosian` is neosian's (DESIGN §40.51 item 1 and
//! the amendments): by a receipt record, by adoption of this installation's
//! own executable (ruling C), or not at all; and a missing receipt rebuilt
//! from the completed moves this home recorded (DESIGN §40.53 item 5).

use super::{Book, NEW, digest, load_at, now};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

/// A neosian stdio registration: no URL, a stdio type, a command whose file
/// name is `neosian` (or `neosian.exe`) and whose first argument is `mcp`.
pub fn recognized(entry: &Value) -> bool {
    let kind = entry.get("type").and_then(Value::as_str);
    if entry.get("url").is_some() || kind.is_some_and(|k| !matches!(k, "stdio" | "local")) {
        return false;
    }
    let (command, first) = match entry.get("command") {
        Some(Value::Array(parts)) => (parts.first(), parts.get(1)),
        Some(command) => (Some(command), entry["args"].get(0)),
        None => return false,
    };
    let name = command
        .and_then(Value::as_str)
        .and_then(|c| c.rsplit(['/', '\\']).next())
        .unwrap_or("")
        .to_ascii_lowercase();
    matches!(name.as_str(), "neosian" | "neosian.exe")
        && first.and_then(Value::as_str) == Some("mcp")
}

pub(super) fn same_file(recorded: &str, file: &Path) -> bool {
    let canonical = |path: &Path| path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    canonical(Path::new(recorded)) == canonical(file)
}

/// How an entry under `neosian` is ours, or that it is not.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ownership {
    /// The receipt records it (done, or pending and equal to the planned entry).
    Recorded,
    /// No record, but it runs this installation's own executable (ruling C):
    /// the receipt is written at the next setup, `mcp install` or refresh.
    Adopted,
    Foreign,
}

/// Whether the entry's command is this installation's own executable (ruling
/// C, restated by the second amendment): an absolute path that resolves to
/// the running binary, or a bare name that resolves to it through PATH. A
/// relative path with a separator depends on the directory the client starts
/// in, so it is never adopted.
pub fn this_installation(entry: &Value) -> bool {
    if !recognized(entry) {
        return false;
    }
    let command = match entry.get("command") {
        Some(Value::Array(parts)) => parts.first().and_then(Value::as_str),
        Some(command) => command.as_str(),
        None => None,
    };
    let Some(command) = command else {
        return false;
    };
    let running = std::env::current_exe().and_then(|p| p.canonicalize());
    let path = Path::new(command);
    let resolved = if path.is_absolute() {
        path.canonicalize().ok()
    } else if command.contains(['/', '\\']) {
        None
    } else {
        let env: std::collections::BTreeMap<String, String> = std::env::vars().collect();
        crate::clients::runner::which(command, &env).and_then(|p| p.canonicalize().ok())
    };
    matches!((running, resolved), (Ok(running), Some(resolved)) if running == resolved)
}

/// The same, with the receipt in the client's directory `dir`.
pub fn owns_in(book: &Book, file: &Path, pointer: &str, entry: &Value) -> bool {
    ownership(book, file, pointer, entry) != Ownership::Foreign
}

/// Each member's digest: a pending record matches an entry holding every
/// planned member unchanged, whatever members the CLI added (J).
pub(super) fn members(entry: &Value) -> Value {
    let digests: serde_json::Map<String, Value> = entry
        .as_object()
        .into_iter()
        .flatten()
        .map(|(k, v)| (k.clone(), json!(digest(v))))
        .collect();
    Value::Object(digests)
}

fn carries(record: &Value, entry: &Value) -> bool {
    let Some(planned) = record["members"].as_object().filter(|m| !m.is_empty()) else {
        return false;
    };
    planned
        .iter()
        .all(|(key, value)| entry.get(key).is_some_and(|v| json!(digest(v)) == *value))
}

/// Whether one receipt record names this entry as ours: a done record while
/// it is still a neosian registration, a pending one while it carries every
/// planned member (R4, J), a rebuilt one while it equals what was written.
fn names(record: &Value, entry: &Value) -> bool {
    if record["digest"] == digest(entry) {
        return true;
    }
    match record["state"].as_str() {
        Some("pending") => carries(record, entry),
        Some("rebuilt") => false,
        _ => recognized(entry),
    }
}

/// How the entry under `neosian` at `pointer` in `file` is ours.
pub fn ownership(book: &Book, file: &Path, pointer: &str, entry: &Value) -> Ownership {
    let Ok(receipt) = load_at(book) else {
        return Ownership::Foreign;
    };
    let recorded = receipt["entries"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|e| {
            e["key"] == NEW
                && e["pointer"] == pointer
                && e["file"].as_str().is_some_and(|f| same_file(f, file))
                && names(e, entry)
        });
    if recorded {
        Ownership::Recorded
    } else if this_installation(entry) {
        Ownership::Adopted
    } else {
        Ownership::Foreign
    }
}

/// The entries under `neosian` the newest completed, not undone, rename run
/// wrote: each counts only while the entry still equals what it wrote.
pub(super) fn rebuilt(book: &Book) -> Vec<Value> {
    let Ok(runs) = std::fs::read_dir(&book.migrations) else {
        return vec![];
    };
    let mut runs: Vec<PathBuf> = runs.filter_map(Result::ok).map(|e| e.path()).collect();
    runs.sort();
    let canonical = |path: &Path| path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let home = book.migrations.parent().map(canonical);
    let client = book.file.parent().map(canonical);
    // Every completed run, oldest first; for each entry, what the latest run
    // that moved it wrote. A run counts only when it names this neosian home
    // and this client's configuration directory (§40.53 item 5).
    let mut entries: std::collections::BTreeMap<(String, String), Value> =
        std::collections::BTreeMap::new();
    for run in &runs {
        let Some(index) = std::fs::read_to_string(run.join("index.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        else {
            continue;
        };
        let recorded = |value: &Value| value.as_str().map(|p| canonical(Path::new(p)));
        if index["state"] != "done" || home.is_none() || recorded(&index["home"]) != home {
            continue;
        }
        for moved in index["moves"].as_array().into_iter().flatten() {
            if recorded(&moved["client_dir"]) != client {
                continue;
            }
            let (Some(file), Some(pointer)) = (moved["file"].as_str(), moved["pointer"].as_str())
            else {
                continue;
            };
            let key = (file.to_owned(), pointer.to_owned());
            if moved["direction"] == "back" {
                entries.remove(&key);
            } else {
                entries.insert(key, moved["entry"].clone());
            }
        }
    }
    entries
        .into_iter()
        .filter(|(_, written)| recognized(written))
        .map(|((file, pointer), written)| {
            json!({"file": file, "pointer": pointer, "key": NEW,
                "digest": digest(&written), "state": "rebuilt", "written_at": now()})
        })
        .collect()
}
