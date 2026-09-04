"""Public memory surface (DESIGN §8). Import as `neosian.memory`.

Re-exports only — the implementation lives in _foundation.memory. The
core names are also on the root package; this module adds the full error
family, the grammar helpers, the Clock and the index generator. It never
imports `.testing` (the conformance kit needs pytest, which is not a
runtime dependency).
"""

from neosian._foundation.memory.actor import (
    ACTOR_MAX_LENGTH,
    ACTOR_PATTERN,
    Actor,
    actor_matches,
    parse_actor,
)
from neosian._foundation.memory.audit import AuditEntry, audit
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import home, project_mounts, project_scope
from neosian._foundation.memory.index import (
    generate_memory_index,
    memory_system_section,
)
from neosian._foundation.memory.maintenance import (
    MaintenanceResult,
    MaintenanceWrite,
    run_maintenance,
)
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.paths import (
    PATH_MAX_LENGTH,
    PATH_MAX_SEGMENTS,
    validate_document_path,
)
from neosian._foundation.memory.portable import (
    ConversationArchive,
    Portable,
    ScopeArchive,
    TransferReport,
    UnitReport,
)
from neosian._foundation.memory.receipt import MemoryWriteReceipt
from neosian._foundation.memory.revert import revert_memory
from neosian._foundation.memory.scope import (
    SCOPE_MAX_LENGTH,
    SCOPE_PATTERN,
    Scope,
    parse_scope,
)
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.memory.transfer import transfer
from neosian._foundation.memory.types import (
    MEMORY_FORMAT_VERSION,
    MemoryAction,
    MemoryDocument,
    MemoryEntry,
    MemoryRedaction,
    MemoryVersion,
)
from neosian._foundation.postgres.store import PostgresStore
from neosian._foundation.server.remote import RemoteStore
from neosian._foundation.shared.clock import Clock, SystemClock
from neosian._foundation.shared.exceptions import (
    MemoryActorInvalidError,
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryEditOnlyMountError,
    MemoryFormatUnsupportedError,
    MemoryPathInvalidError,
    MemoryReadOnlyMountError,
    MemoryScopeInvalidError,
    MemoryStoreError,
)

__all__ = [
    "ACTOR_MAX_LENGTH",
    "ACTOR_PATTERN",
    "Actor",
    "AuditEntry",
    "MEMORY_FORMAT_VERSION",
    "PATH_MAX_LENGTH",
    "PATH_MAX_SEGMENTS",
    "SCOPE_MAX_LENGTH",
    "SCOPE_PATTERN",
    "Clock",
    "ConversationArchive",
    "FileStore",
    "MaintenanceResult",
    "MaintenanceWrite",
    "MemoryAction",
    "MemoryActorInvalidError",
    "MemoryConfig",
    "MemoryConflictError",
    "MemoryDocument",
    "MemoryDocumentNotFoundError",
    "MemoryEditOnlyMountError",
    "MemoryEntry",
    "MemoryFormatUnsupportedError",
    "MemoryPathInvalidError",
    "MemoryReadOnlyMountError",
    "MemoryRedaction",
    "MemoryScopeInvalidError",
    "MemoryStore",
    "MemoryStoreError",
    "MemoryVersion",
    "MemoryWriteReceipt",
    "Mount",
    "Portable",
    "PostgresStore",
    "RemoteStore",
    "Scope",
    "ScopeArchive",
    "SystemClock",
    "TransferReport",
    "UnitReport",
    "actor_matches",
    "audit",
    "create_memory_tool",
    "generate_memory_index",
    "home",
    "memory_system_section",
    "parse_actor",
    "parse_scope",
    "project_mounts",
    "project_scope",
    "revert_memory",
    "run_maintenance",
    "transfer",
    "validate_document_path",
]
