"""Shared fixtures for the MCP transport unit tests (keyless)."""

from pathlib import Path

import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount

USER = Mount(scope="user:demo", mount_path="memories", description="user facts")
KB = Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True)


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "memory")


@pytest.fixture
def config(store: FileStore) -> MemoryConfig:
    return MemoryConfig(store=store, mounts=(USER, KB))
