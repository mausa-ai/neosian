"""FileStore's `Portable` side: enumeration over the root, the verbatim
restore's write order and modes, the occupied gate (NC4, §26)."""

from __future__ import annotations

import dataclasses
import stat
from typing import TYPE_CHECKING

import pytest

from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.portable import ConversationArchive, ScopeArchive
from neosian._foundation.memory.transfer import archive_conversation, archive_scope
from neosian._foundation.shared.exceptions import (
    ConversationConflictError,
    MemoryConflictError,
    MemoryPathInvalidError,
)
from tests.support.clock import ManualClock

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_MESSAGES = (Message(role=Role.USER, content="hi"),)
_ENTRY = ConversationProjection(turn=1, kind="log", text="x")


class TestEnumeration:
    async def test_scopes_lists_what_holds_state(self, store: FileStore) -> None:
        await store.write("user:a", "x", "1")
        await store.write("user:a/proj:p", "y", "2")
        await store.write("user:gone", "z", "3")
        await store.delete("user:gone", "z")  # history only — still a scope
        await store.redact("team:t")  # nothing matched, nothing written
        assert await store.scopes() == ("user:a", "user:a/proj:p", "user:gone")

    async def test_scopes_skips_what_is_not_a_scope(self, store: FileStore) -> None:
        root = store._root  # noqa: SLF001 — the substrate
        for name in ("conversations", "spool", "notes", "bad%3A name", "user%3Aa"):
            (root / name / "documents").mkdir(parents=True)
            (root / name / "documents" / "x.md").write_text("")
        await store.append_turn("c1", _MESSAGES)
        assert await store.scopes() == ("user:a",)

    async def test_conversations_skips_foreign_names(self, store: FileStore) -> None:
        await store.append_turn("keep-1", _MESSAGES)
        await store.append_projections("proj-only", (_ENTRY,))
        root = store._root  # noqa: SLF001
        (root / "conversations" / "a b").mkdir(parents=True)
        (root / "conversations" / "empty").mkdir()
        assert await store.conversations() == ("keep-1", "proj-only")


class TestRestore:
    async def test_writes_sidecars_trail_and_documents_privately(
        self, store: FileStore, tmp_path: Path, manual_clock: ManualClock
    ) -> None:
        await store.write("user:a", "n/x", "body", actor="me")
        await store.redact("user:a", path="n/x", actor="ops")
        await store.write("user:a", "n/x", "again")
        archive = await archive_scope(store, "user:a")
        target = FileStore(tmp_path / "t", clock=manual_clock)
        await target.restore_scope(archive)
        scope_dir = target._root / "user%3Aa"  # noqa: SLF001
        files = sorted(
            p.relative_to(scope_dir).as_posix()
            for p in scope_dir.rglob("*")
            if p.is_file()
        )
        assert files == ["documents/n/x.md", "redactions.jsonl", "versions/n/x.jsonl"]
        for file in scope_dir.rglob("*"):
            mode = stat.S_IMODE(file.stat().st_mode)
            assert mode == (0o700 if file.is_dir() else 0o600), file
        assert await target.read("user:a", "n/x") == await store.read("user:a", "n/x")
        assert await target.history("user:a") == await store.history("user:a")
        assert await target.redactions("user:a") == await store.redactions("user:a")

    async def test_an_empty_archive_creates_nothing(self, store: FileStore) -> None:
        await store.restore_scope(ScopeArchive("user:z", (), (), ()))
        await store.restore_conversation(ConversationArchive("nothing", (), ()))
        root = store._root  # noqa: SLF001
        assert not (root / "user%3Az").exists()
        assert not (root / "conversations").exists()
        assert await store.scopes() == ()

    async def test_occupied_gates(self, store: FileStore, tmp_path: Path) -> None:
        await store.write("user:a", "x", "1")
        await store.append_turn("c", _MESSAGES)
        scope = await archive_scope(store, "user:a")
        conversation = await archive_conversation(store, "c")
        with pytest.raises(MemoryConflictError, match="target_occupied"):
            await store.restore_scope(scope)
        with pytest.raises(ConversationConflictError, match="target_occupied"):
            await store.restore_conversation(conversation)
        other = FileStore(tmp_path / "o")
        await other.restore_scope(scope)
        await other.restore_conversation(conversation)
        with pytest.raises(MemoryConflictError):
            await other.restore_scope(scope)
        with pytest.raises(ConversationConflictError):
            await other.restore_conversation(conversation)
        assert await other.read_turns("c") == await store.read_turns("c")

    async def test_paths_are_validated_before_anything_lands(
        self, store: FileStore
    ) -> None:
        await store.write("user:a", "x", "1")
        archive = await archive_scope(store, "user:a")
        rows = tuple(
            dataclasses.replace(row, path="../escape") for row in archive.versions
        )
        with pytest.raises(MemoryPathInvalidError):
            await store.restore_scope(ScopeArchive("user:b", (), rows, ()))
        assert not (store._root / "user%3Ab").exists()  # noqa: SLF001
