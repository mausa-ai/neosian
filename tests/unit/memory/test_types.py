"""Tests for the memory value types (memory/types.py)."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from neosian._foundation.memory.types import (
    MEMORY_FORMAT_VERSION,
    MemoryDocument,
    MemoryEntry,
    MemoryVersion,
)

_T = datetime(2026, 8, 19, tzinfo=UTC)


def _document() -> MemoryDocument:
    return MemoryDocument(
        scope="user:1",
        path="doc",
        content="x",
        version=1,
        created_at=_T,
        updated_at=_T,
        actor=None,
    )


class TestValueTypes:
    def test_format_version_is_one(self) -> None:
        assert MEMORY_FORMAT_VERSION == 1

    def test_document_is_frozen_with_slots(self) -> None:
        document = _document()
        with pytest.raises(FrozenInstanceError):
            document.content = "y"  # type: ignore[misc]
        assert not hasattr(document, "__dict__")

    def test_document_defaults(self) -> None:
        document = _document()
        assert document.redacted is False
        assert dict(document.extra) == {}
        with pytest.raises(TypeError):
            document.extra["k"] = "v"  # type: ignore[index]

    def test_entry_and_version_are_frozen_with_slots(self) -> None:
        entry = MemoryEntry(path="p", version=1, created_at=_T, updated_at=_T)
        row = MemoryVersion(
            path="p",
            version=1,
            action="created",
            content="",
            actor=None,
            created_at=_T,
        )
        for value in (entry, row):
            assert not hasattr(value, "__dict__")
        assert entry.redacted is False
        assert row.redacted is False

    def test_field_lists_are_the_documented_contract(self) -> None:
        assert [f.name for f in fields(MemoryDocument)] == [
            "scope",
            "path",
            "content",
            "version",
            "created_at",
            "updated_at",
            "actor",
            "redacted",
            "extra",
        ]
        assert [f.name for f in fields(MemoryEntry)] == [
            "path",
            "version",
            "created_at",
            "updated_at",
            "redacted",
        ]
        assert [f.name for f in fields(MemoryVersion)] == [
            "path",
            "version",
            "action",
            "content",
            "actor",
            "created_at",
            "redacted",
        ]
