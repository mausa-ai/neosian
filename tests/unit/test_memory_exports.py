"""The neosian.memory facade: pinned surface, lazily loaded (DESIGN §1, §8)."""

import pathlib
import subprocess
import sys

import pytest


@pytest.mark.unit
def test_memory_all_is_pinned() -> None:
    import neosian.memory

    assert neosian.memory.__all__ == [
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
        "Page",
        "Pageable",
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
    for name in neosian.memory.__all__:
        assert getattr(neosian.memory, name) is not None


@pytest.mark.unit
def test_memory_testing_exports_the_contract_kit() -> None:
    import neosian.memory.testing

    assert neosian.memory.testing.__all__ == [
        "ConcurrencyContract",
        "LedgerContract",
        "MemoryStoreContract",
    ]


@pytest.mark.unit
def test_root_import_does_not_load_memory_facade() -> None:
    """`import neosian` must not pull in neosian.memory (lazy, excludable)."""
    code = "import neosian, sys; assert 'neosian.memory' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_memory_facade_does_not_load_the_testing_kit() -> None:
    """pytest must never become a runtime dependency of neosian.memory."""
    code = (
        "import neosian.memory, sys; "
        "assert 'neosian.memory.testing' not in sys.modules; "
        "assert 'neosian._foundation.memory.testing' not in sys.modules; "
        "assert 'pytest' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_every_contract_suite_reaches_the_kit_through_the_facade() -> None:
    """TP-16: the seam hosts are told to use is the seam we use.

    Six of eight call sites imported `neosian._foundation.*.testing`
    directly, so the public facade was never exercised by the default
    (unit) tier — the review's finding. Planting a kit at the private path
    is still legal for the library's own internals; importing one from a
    test suite is not.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = [
        f"{path.relative_to(root.parent)}:{number}"
        for path in sorted(root.rglob("test_*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if line.startswith("from neosian._foundation.")
        and (".memory.testing" in line or ".conversation.testing" in line)
    ]
    assert offenders == [], offenders
