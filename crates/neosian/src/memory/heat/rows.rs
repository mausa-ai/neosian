//! The log's two row shapes and their JSON lines: a reader's first read
//! of a path, and a fold of the readers older than the month.

use std::fs;
use std::io;
use std::path::Path;

use serde_json::{Value, json};

use crate::store::time::Stamp;

/// A row: one reader's first read of a path, or a fold of the readers
/// older than the month.
pub(super) enum Row {
    Read {
        at: Stamp,
        reader: String,
        path: String,
    },
    Fold {
        path: String,
        readers: u64,
        last: Stamp,
    },
}

impl Row {
    pub(super) fn path(&self) -> &str {
        match self {
            Self::Read { path, .. } | Self::Fold { path, .. } => path,
        }
    }

    pub(super) fn relabel(self, to: &str) -> Self {
        match self {
            Self::Read { at, reader, .. } => Self::Read {
                at,
                reader,
                path: to.to_string(),
            },
            Self::Fold { readers, last, .. } => Self::Fold {
                path: to.to_string(),
                readers,
                last,
            },
        }
    }

    pub(super) fn line(&self) -> String {
        let value = match self {
            Self::Read { at, reader, path } => {
                json!({"at": at.utc().z(), "reader": reader, "path": path})
            }
            Self::Fold {
                path,
                readers,
                last,
            } => json!({"path": path, "readers": readers, "last": last.utc().z()}),
        };
        format!("{value}\n")
    }
}

/// A row from its line; a foreign or malformed line is never ours to read.
fn parse(line: &str) -> Option<Row> {
    let value: Value = serde_json::from_str(line).ok()?;
    let path = value.get("path")?.as_str()?.to_string();
    if let Some(readers) = value.get("readers").and_then(Value::as_u64) {
        let last = Stamp::parse(value.get("last")?.as_str()?).ok()?;
        return Some(Row::Fold {
            path,
            readers,
            last,
        });
    }
    let at = Stamp::parse(value.get("at")?.as_str()?).ok()?;
    let reader = value.get("reader")?.as_str()?.to_string();
    Some(Row::Read { at, reader, path })
}

pub(super) fn load(file: &Path) -> io::Result<Vec<Row>> {
    Ok(fs::read_to_string(file)?
        .lines()
        .filter_map(parse)
        .collect())
}
