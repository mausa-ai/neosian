//! The memory store's errors (DESIGN §5, §8): the codes and the message
//! text of `shared/exceptions/memory.py`, byte for byte, since both doors
//! print them.

use std::io;

use crate::repr::repr;

/// One refusal of the store or the tool layer: a code of the `memory_`
/// family and the developer-facing sentence.
#[derive(Debug)]
pub struct MemoryError {
    pub code: &'static str,
    pub message: String,
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
        }
    }

    pub fn scope_invalid(scope: &str, reason: &str) -> Self {
        Self {
            code: "memory_scope_invalid",
            message: format!("Invalid memory scope {}: {reason}", repr(scope)),
        }
    }

    pub fn path_invalid(path: &str, reason: &str) -> Self {
        Self {
            code: "memory_path_invalid",
            message: format!("Invalid memory document path {}: {reason}", repr(path)),
        }
    }

    /// `path` is None where the scope itself is what collided.
    pub fn conflict(scope: &str, path: Option<&str>, reason: &str) -> Self {
        let place = match path {
            Some(path) => format!("on {} in scope {}", repr(path), repr(scope)),
            None => format!("in scope {}", repr(scope)),
        };
        Self {
            code: "memory_conflict",
            message: format!("Memory conflict {place}: {reason}"),
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
        }
    }

    /// Raised by the tool layer alone: a mount is policy the store never
    /// sees (C7).
    pub fn read_only(mount_path: &str) -> Self {
        Self {
            code: "memory_read_only_mount",
            message: format!("Memory mount {} is read-only", repr(mount_path)),
        }
    }

    pub fn edit_only(mount_path: &str) -> Self {
        let mount = repr(mount_path);
        Self {
            code: "memory_edit_only_mount",
            message: format!("Memory mount {mount} is edit-only: its document set is fixed"),
        }
    }
}

/// What a store call fails with: a refusal the caller renders with its
/// code, or the filesystem's own error.
#[derive(Debug)]
pub enum Error {
    Memory(MemoryError),
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
