"""Public memory surface (DESIGN §8). Import as `neosian.memory`.

Re-exports only — the implementation lives in _foundation.memory. The
core names are also on the root package; this module adds the full error
family, the grammar helpers and the Clock. It never imports `.testing`
(the conformance kit needs pytest, which is not a runtime dependency).
"""

from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.paths import (
    PATH_MAX_LENGTH,
    PATH_MAX_SEGMENTS,
    validate_document_path,
)
from neosian._foundation.memory.scope import (
    SCOPE_MAX_LENGTH,
    SCOPE_PATTERN,
    Scope,
    parse_scope,
)
from neosian._foundation.memory.types import (
    MEMORY_FORMAT_VERSION,
    MemoryAction,
    MemoryDocument,
    MemoryEntry,
    MemoryVersion,
)
from neosian._foundation.shared.clock import Clock, SystemClock
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryFormatUnsupportedError,
    MemoryPathInvalidError,
    MemoryReadOnlyMountError,
    MemoryScopeInvalidError,
    MemoryStoreError,
)

__all__ = [
    "MEMORY_FORMAT_VERSION",
    "PATH_MAX_LENGTH",
    "PATH_MAX_SEGMENTS",
    "SCOPE_MAX_LENGTH",
    "SCOPE_PATTERN",
    "Clock",
    "FileStore",
    "MemoryAction",
    "MemoryConflictError",
    "MemoryDocument",
    "MemoryDocumentNotFoundError",
    "MemoryEntry",
    "MemoryFormatUnsupportedError",
    "MemoryPathInvalidError",
    "MemoryReadOnlyMountError",
    "MemoryScopeInvalidError",
    "MemoryStore",
    "MemoryStoreError",
    "MemoryVersion",
    "Scope",
    "SystemClock",
    "parse_scope",
    "validate_document_path",
]
