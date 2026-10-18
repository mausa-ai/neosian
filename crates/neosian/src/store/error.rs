//! The memory store's errors (DESIGN §5, §8): the codes, the message
//! text and the typed details of `shared/exceptions/memory.py`, byte for
//! byte, since both doors print them and the wire carries the details so
//! a client rebuilds the exact exception (§18.3).

use std::io;

use serde_json::{Value, json};

use crate::repr::repr;

/// One refusal of the store or the tool layer: a code of the `memory_`
/// family, the developer-facing sentence, and the fields it was built from.
#[derive(Debug)]
pub struct MemoryError {
    pub code: &'static str,
    pub message: String,
    pub details: Value,
}

impl MemoryError {
    pub fn not_found(scope: &str, path: &str) -> Self {
        Self {
            code: "memory_document_not_found",
            message: format!(
                "Memory document not found: {} in scope {}",
                repr(path),
                repr(scope)
            ),
            details: json!({"scope": scope, "path": path}),
        }
    }

    pub fn scope_invalid(scope: &str, reason: &str) -> Self {
        Self {
            code: "memory_scope_invalid",
            message: format!("Invalid memory scope {}: {reason}", repr(scope)),
            details: json!({"scope": scope, "reason": reason}),
        }
    }

    pub fn path_invalid(path: &str, reason: &str) -> Self {
        Self {
            code: "memory_path_invalid",
            message: format!("Invalid memory document path {}: {reason}", repr(path)),
            details: json!({"path": path, "reason": reason}),
        }
    }

    /// `path` is None where the scope itself is what collided.
    pub fn conflict(scope: &str, path: Option<&str>, reason: &str) -> Self {
        Self::conflict_versions(scope, path, reason, None, None)
    }

    /// A lost `expected_version` race: the version the caller expected and
    /// the one found (None for an absent document).
    pub fn conflict_versions(
        scope: &str,
        path: Option<&str>,
        reason: &str,
        expected: Option<u64>,
        actual: Option<u64>,
    ) -> Self {
        let place = match path {
            Some(path) => format!("on {} in scope {}", repr(path), repr(scope)),
            None => format!("in scope {}", repr(scope)),
        };
        Self {
            code: "memory_conflict",
            message: format!("Memory conflict {place}: {reason}"),
            details: json!({
                "scope": scope,
                "path": path,
                "reason": reason,
                "expected_version": expected,
                "actual_version": actual,
            }),
        }
    }

    pub fn format_unsupported(scope: &str, path: &str, reason: &str) -> Self {
        Self {
            code: "memory_format_unsupported",
            message: format!(
                "Unsupported memory format for {} in scope {}: {reason}",
                repr(path),
                repr(scope)
            ),
            details: json!({"scope": scope, "path": path, "reason": reason}),
        }
    }

    /// Raised by the tool layer alone: a mount is policy the store never
    /// sees (C7).
    pub fn read_only(mount_path: &str) -> Self {
        Self {
            code: "memory_read_only_mount",
            message: format!("Memory mount {} is read-only", repr(mount_path)),
            details: json!({"mount_path": mount_path}),
        }
    }

    pub fn edit_only(mount_path: &str) -> Self {
        let mount = repr(mount_path);
        Self {
            code: "memory_edit_only_mount",
            message: format!("Memory mount {mount} is edit-only: its document set is fixed"),
            details: json!({"mount_path": mount_path}),
        }
    }
}

/// What a store call fails with: a refusal the caller renders with its
/// code, a `ValueError` of the Python side (its sentence, no code: a
/// malformed cursor, a page limit below one), or the filesystem's own error.
#[derive(Debug)]
pub enum Error {
    Memory(MemoryError),
    Value(String),
    Io(io::Error),
}

impl From<MemoryError> for Error {
    fn from(error: MemoryError) -> Self {
        Self::Memory(error)
    }
}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}
