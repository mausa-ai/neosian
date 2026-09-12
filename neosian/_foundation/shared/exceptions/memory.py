"""The memory store's errors (DESIGN §5 table, §8)."""

from __future__ import annotations

from neosian._foundation.shared.exceptions.base import NeosianError


# Memory Errors (DESIGN §5 table, §8). Base is MemoryStoreError — never
# MemoryError, which shadows a Python builtin in __all__. Messages are
# inline f-strings: constants.py is named debt and never grows.
class MemoryStoreError(NeosianError):
    """Base exception for memory-store errors."""

    code = "memory_error"


class MemoryDocumentNotFoundError(MemoryStoreError):
    """Raised when an operation requires a document that does not exist."""

    code = "memory_document_not_found"

    def __init__(self, scope: str, path: str) -> None:
        super().__init__(
            f"Memory document not found: {path!r} in scope {scope!r}",
            details={"scope": scope, "path": path},
        )
        self.scope = scope
        self.path = path


class MemoryScopeInvalidError(MemoryStoreError):
    """Raised when a scope string violates the grammar (ECOSYSTEM §2)."""

    code = "memory_scope_invalid"

    def __init__(self, scope: str, reason: str) -> None:
        super().__init__(
            f"Invalid memory scope {scope!r}: {reason}",
            details={"scope": scope, "reason": reason},
        )
        self.scope = scope
        self.reason = reason


class MemoryActorInvalidError(MemoryStoreError):
    """Raised when an actor string violates the grammar (DESIGN §20).

    Raised only by neosian's own writers, the shell and the daemon — the
    store ABC keeps actors opaque (ECOSYSTEM §2's convention rule).
    """

    code = "memory_actor_invalid"

    def __init__(self, actor: str, reason: str) -> None:
        super().__init__(
            f"Invalid actor {actor!r}: {reason}",
            details={"actor": actor, "reason": reason},
        )
        self.actor = actor
        self.reason = reason


class MemoryPathInvalidError(MemoryStoreError):
    """Raised when a document path violates the grammar (DESIGN §8)."""

    code = "memory_path_invalid"

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"Invalid memory document path {path!r}: {reason}",
            details={"path": path, "reason": reason},
        )
        self.path = path
        self.reason = reason


class MemoryConflictError(MemoryStoreError):
    """Raised when a write loses a version race or a rename target is taken.

    `reason` is machine-checkable: "version_mismatch", "document_absent"
    (an `expected_version` on a document that does not exist),
    "destination_exists" (rename onto an occupied path, src == dst
    included), "revert_stale" (`revert_memory` targeting a version the
    document has already moved past — NP), or "target_occupied" (a
    verbatim restore into a scope that already holds documents, history
    or redactions — NC4, `path=None`), or "case_collision" (NQ2, MC-13: a
    case-folding filesystem already holds this scope or path under a
    different spelling, and merging them would share one version counter;
    `path=None` when the scope segment is the one that folded). Reasons
    are documented here and in DESIGN §8, appended together.
    """

    code = "memory_conflict"

    def __init__(
        self,
        scope: str,
        path: str | None,
        reason: str,
        *,
        expected_version: int | None = None,
        actual_version: int | None = None,
    ) -> None:
        where = (
            f"on {path!r} in scope {scope!r}"
            if path is not None
            else f"in scope {scope!r}"
        )
        super().__init__(
            f"Memory conflict {where}: {reason}",
            details={
                "scope": scope,
                "path": path,
                "reason": reason,
                "expected_version": expected_version,
                "actual_version": actual_version,
            },
        )
        self.scope = scope
        self.path = path
        self.reason = reason
        self.expected_version = expected_version
        self.actual_version = actual_version


class MemoryFormatUnsupportedError(MemoryStoreError):
    """Raised when stored data declares a newer format than this library reads.

    Refusal, never coercion: a newer `neosian_format` (or a missing/broken
    storage envelope, or a naive timestamp) is rejected at the boundary.
    """

    code = "memory_format_unsupported"

    def __init__(self, scope: str, path: str, reason: str) -> None:
        super().__init__(
            f"Unsupported memory format for {path!r} in scope {scope!r}: {reason}",
            details={"scope": scope, "path": path, "reason": reason},
        )
        self.scope = scope
        self.path = path
        self.reason = reason


class MemoryReadOnlyMountError(MemoryStoreError):
    """Raised by the tool layer when a write targets a read-only mount.

    The store never raises this (C7: mounts are tool-layer policy); it lives
    here so the DESIGN §5 code table ships whole. Wired in N1 slice B.
    """

    code = "memory_read_only_mount"

    def __init__(self, mount_path: str) -> None:
        super().__init__(
            f"Memory mount {mount_path!r} is read-only",
            details={"mount_path": mount_path},
        )
        self.mount_path = mount_path


class MemoryEditOnlyMountError(MemoryStoreError):
    """Raised by the tool layer when an edit-only mount's document set would change.

    Edit-only fixes the *set* of documents, never their contents: creating a
    new path, deleting, or renaming is refused; overwrites and in-place edits
    pass. The store never raises this (C7: mounts are tool-layer policy).
    Wired in NP slice B.
    """

    code = "memory_edit_only_mount"

    def __init__(self, mount_path: str) -> None:
        super().__init__(
            f"Memory mount {mount_path!r} is edit-only: its document set is fixed",
            details={"mount_path": mount_path},
        )
        self.mount_path = mount_path
