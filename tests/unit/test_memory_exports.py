"""The neosian.memory facade: pinned surface, lazily loaded (DESIGN §1, §8)."""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_memory_all_is_pinned() -> None:
    import neosian.memory

    assert neosian.memory.__all__ == [
        "MEMORY_FORMAT_VERSION",
        "PATH_MAX_LENGTH",
        "PATH_MAX_SEGMENTS",
        "SCOPE_MAX_LENGTH",
        "SCOPE_PATTERN",
        "Clock",
        "FileStore",
        "MemoryAction",
        "MemoryConfig",
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
        "Mount",
        "PostgresStore",
        "Scope",
        "SystemClock",
        "create_memory_tool",
        "generate_memory_index",
        "memory_system_section",
        "parse_scope",
        "validate_document_path",
    ]
    for name in neosian.memory.__all__:
        assert getattr(neosian.memory, name) is not None


@pytest.mark.unit
def test_memory_testing_exports_the_contract_kit() -> None:
    import neosian.memory.testing

    assert neosian.memory.testing.__all__ == ["MemoryStoreContract"]


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
