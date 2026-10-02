"""The SessionStart rendering rule (§21.7, §33): which sessions, in what
order, under what budget; the pending note; the per-client ceiling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.sessions import HANDOFF_PATH, handoff_document
from neosian._foundation.memory.settings import DEFAULT_SCHEMA, StoreSettings
from neosian._foundation.memory.types import MemoryEntry
from neosian._foundation.record.context import (
    CONTEXT_CHARS,
    RECENT_SESSIONS,
    choose_sessions,
    render_left_off,
    render_note,
    render_session_start,
)
from neosian._foundation.record.settings import RecordSettings
from neosian._foundation.shared.prompt_assets import get_prompt

_T0 = datetime(2026, 9, 3, tzinfo=UTC)
_NOW = datetime(2026, 10, 3, 12, 30, tzinfo=UTC)


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
            store, store, "user:me", own="x", source="startup", budget_chars=600
        )
        assert len(block) <= 600  # the frames count too (§33)
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

    async def test_a_long_token_renders_as_a_qualified_handle(
        self, tmp_path: Path
    ) -> None:
        url = "https://docs.example.test/specs/0f8fad5b-d9cb-469f-a165-70867728950e/v2"
        store = await _seed(tmp_path, "s1", f"the spec is at {url}")
        block = await render_left_off(store, store, "user:me", own="x", source="")
        assert "[1] USER: the spec is at [link s1:1] | AGENT: ok" in block
        assert url not in block


async def _baton(store: FileStore, *, age_days: int, **fields: object) -> None:
    await store.write(
        "user:me",
        HANDOFF_PATH,
        handoff_document(
            actor="claude-code:s1",
            written=_NOW - timedelta(days=age_days),
            note="Next: run the gates.",
            **fields,  # type: ignore[arg-type]
        ),
        actor="t",
    )


class TestRenderNote:
    async def test_a_young_pending_note_is_shown_whole_naming_its_call(
        self, tmp_path: Path
    ) -> None:
        store = FileStore(tmp_path)
        assert (
            await render_note(store, "user:me", mount_path="project", now=_NOW) is None
        )
        await _baton(store, age_days=1, conversation="s1")
        block = await render_note(store, "user:me", mount_path="project", now=_NOW)
        assert block is not None
        head, note, end = block.splitlines()
        assert head.startswith(
            "[handoff note — written 2026-10-02T12:30Z by claude-code:s1"
        )
        assert 'continue_session(conversation="s1")' in head
        assert note == "Next: run the gates." and end == "[end of note]"

    async def test_an_unlinked_note_names_the_bare_call(self, tmp_path: Path) -> None:
        store = FileStore(tmp_path)
        await _baton(store, age_days=0)
        block = await render_note(store, "user:me", mount_path="project", now=_NOW)
        assert block is not None and "continue_session()" in block

    async def test_a_stale_note_is_one_line_and_a_picked_up_one_nothing(
        self, tmp_path: Path
    ) -> None:
        store = FileStore(tmp_path)
        await _baton(store, age_days=8, conversation="s1")
        line = await render_note(store, "user:me", mount_path="project", now=_NOW)
        assert line is not None and "\n" not in line
        assert line.startswith(
            "[a handoff note from claude-code:s1 (2026-09-25T12:30Z)"
        )
        assert "view /project/handoff" in line
        await _baton(store, age_days=0, picked_up=(_NOW, "mcp:opencode"))
        assert (
            await render_note(store, "user:me", mount_path="project", now=_NOW) is None
        )


def _settings(root: Path, agent: str) -> RecordSettings:
    mount = Mount("user:me", "project")
    store = StoreSettings(
        mounts=(mount,), root=root, dsn=None, schema=DEFAULT_SCHEMA, actor="t"
    )
    return RecordSettings(store=store, mount=mount, agent=agent, spool=root / "spool")


class TestRenderSessionStart:
    async def test_the_note_sits_between_the_index_and_the_sessions(
        self, tmp_path: Path
    ) -> None:
        store = await _seed(tmp_path, "s1", "turn 1")
        await _baton(store, age_days=0, conversation="s1")
        text = await render_session_start(
            store,
            store,
            _settings(tmp_path, "claude-code"),
            session_id="x",
            source="startup",
            now=_NOW,
        )
        index = text.index(get_prompt("context.start_index"))
        note = text.index("[handoff note —")
        sessions = text.index(get_prompt("context.start_header"))
        assert index < note < sessions
        compact = await render_session_start(
            store,
            store,
            _settings(tmp_path, "claude-code"),
            session_id="s1",
            source="compact",
            now=_NOW,
        )
        assert "[handoff note" not in compact and "[1] USER: turn 1" in compact

    async def test_each_client_gets_at_most_its_ceiling(self, tmp_path: Path) -> None:
        """A busy scope: sixty documents, three sessions of forty long
        turns and a note — every client's block stays under its cap, the
        index under three eighths of it, and nothing is lost: the folds say
        what is hidden."""
        store = FileStore(tmp_path)
        for i in range(60):
            await store.write("user:me", f"notes/topic-{i:02d}-{'x' * 60}", "body")
        for session in ("s1", "s2", "s3"):
            await _seed(tmp_path, session, *(f"turn {i} " * 40 for i in range(40)))
        await _baton(store, age_days=0, conversation="s1")
        for agent, ceiling in CONTEXT_CHARS.items():
            text = await render_session_start(
                store,
                store,
                _settings(tmp_path, agent),
                session_id="x",
                source="startup",
                now=_NOW,
            )
            assert len(text) <= ceiling, agent
            head, _, rest = text.partition("\n\n[handoff note")
            assert len(head) <= ceiling * 3 // 8 + 120, agent
            assert "earlier turns not shown" in rest, agent  # folded, never cut
        bare = await render_session_start(
            store,
            store,
            _settings(tmp_path, "unknown"),
            session_id="x",
            source="startup",
            now=_NOW,
        )
        assert len(bare) <= 10_000
