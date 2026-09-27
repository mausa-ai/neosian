"""The sessions vocabulary (§20.9, §32): the document, the mount rules,
the listing every reader shares."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.sessions import (
    SESSIONS_PREFIX,
    project_mount,
    session_ids,
    sessions_document,
    sessions_path,
)

SESSION = "cc-7f3a"
SCOPE = "user:me/proj:d"


class TestSessionsDocument:
    def test_the_document_is_small_and_first_line_only(self) -> None:
        started = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
        text = sessions_document(
            agent="claude-code",
            session_id=SESSION,
            started=started,
            last_prompt="first line\nsecond line",
            turns=3,
        )
        assert text.startswith(f"# claude-code session {SESSION}\n")
        assert "- started: 2026-09-02T10:00:00Z\n" in text
        assert "- last prompt: first line\n" in text and "second" not in text
        assert text.endswith("- turns: 3\n")

    def test_no_prompt_is_a_dash(self) -> None:
        text = sessions_document(
            agent="a",
            session_id="s",
            started=datetime.now(UTC),
            last_prompt=None,
            turns=1,
        )
        assert "- last prompt: -\n" in text

    def test_the_path_is_under_sessions(self) -> None:
        assert sessions_path(SESSION) == f"{SESSIONS_PREFIX}{SESSION}"


class TestProjectMount:
    def test_the_mount_at_project_whatever_its_flags(self, tmp_path: Path) -> None:
        store = FileStore(tmp_path)
        user = Mount(scope="user:me", mount_path="user")
        frozen = Mount(scope=SCOPE, mount_path="project", read_only=True)
        assert project_mount(MemoryConfig(store=store, mounts=(user, frozen))) is frozen
        assert project_mount(MemoryConfig(store=store, mounts=(user,))) is None
        assert project_mount(None) is None


class TestSessionIds:
    async def test_listed_in_path_order_without_redacted_or_nested(
        self, tmp_path: Path
    ) -> None:
        store = FileStore(tmp_path)
        for path in ("sessions/b", "sessions/a", "sessions/gone", "notes/x"):
            await store.write(SCOPE, path, "x", actor="cli:t")
        await store.write(SCOPE, "sessions/nested/c", "x", actor="cli:t")
        await store.redact(SCOPE, path="sessions/gone", actor="cli:t")
        assert await session_ids(store, SCOPE) == ("a", "b")

    async def test_an_empty_scope_lists_nothing(self, tmp_path: Path) -> None:
        assert await session_ids(FileStore(tmp_path), SCOPE) == ()
