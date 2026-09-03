"""The SessionStart rendering rule (§21.7): which sessions, in what
order, under what budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.types import MemoryEntry
from neosian._foundation.record.context import (
    RECENT_SESSIONS,
    choose_sessions,
    render_left_off,
)
from neosian._foundation.shared.prompt_assets import get_prompt

_T0 = datetime(2026, 9, 3, tzinfo=UTC)


def _entry(path: str, age: int, *, redacted: bool = False) -> MemoryEntry:
    stamp = _T0 - timedelta(minutes=age)
    return MemoryEntry(
        path=path, version=1, created_at=stamp, updated_at=stamp, redacted=redacted
    )


class TestChooseSessions:
    def test_newest_first_capped_at_the_limit(self) -> None:
        entries = [_entry(f"sessions/s{i}", age=i) for i in range(5)]
        chosen = choose_sessions(entries, own="s9", source="startup")
        assert chosen == ["s0", "s1", "s2"] and len(chosen) == RECENT_SESSIONS

    def test_only_sessions_documents_and_never_a_redacted_one(self) -> None:
        entries = [
            _entry("notes/todo", age=0),
            _entry("sessions/gone", age=1, redacted=True),
            _entry("sessions/kept", age=2),
        ]
        assert choose_sessions(entries, own="x", source="resume") == ["kept"]

    def test_compact_is_the_own_session_when_recorded(self) -> None:
        entries = [_entry("sessions/a", age=0), _entry("sessions/b", age=1)]
        assert choose_sessions(entries, own="b", source="compact") == ["b"]
        assert choose_sessions(entries, own="c", source="compact") == ["a", "b"]

    def test_the_own_session_is_one_of_the_recent_on_startup(self) -> None:
        entries = [_entry("sessions/a", age=0), _entry("sessions/b", age=1)]
        assert choose_sessions(entries, own="b", source="startup") == ["a", "b"]


async def _seed(root: Path, session: str, *turns: str) -> FileStore:
    store = FileStore(root)
    for text in turns:
        await store.append_turn(
            session,
            [
                Message(role=Role.USER, content=text),
                Message(role=Role.ASSISTANT, content="ok"),
            ],
            actor=f"claude-code:{session}",
        )
    await store.write("user:me", f"sessions/{session}", "# session", actor="t")
    return store


class TestRenderLeftOff:
    async def test_the_budget_is_shared_and_folds_the_oldest(
        self, tmp_path: Path
    ) -> None:
        store = await _seed(tmp_path, "s1", *(f"turn {i}" for i in range(1, 7)))
        block = await render_left_off(
            store, store, "user:me", own="x", source="startup", budget_chars=120
        )
        lines = block.splitlines()
        assert lines[0] == get_prompt("context.start_header")
        assert lines[1] == "[conversation s1 — written by claude-code:s1]"
        assert "earlier turns not shown" in lines[2]  # the fold, never deletion
        assert lines[-2].startswith("[6] USER: turn 6")
        assert lines[-1] == get_prompt("context.start_footer")

    async def test_a_listed_session_without_turns_is_skipped(
        self, tmp_path: Path
    ) -> None:
        store = FileStore(tmp_path)
        await store.write("user:me", "sessions/ghost", "# session", actor="t")
        block = await render_left_off(store, store, "user:me", own="x", source="")
        assert get_prompt("context.start_empty") in block
