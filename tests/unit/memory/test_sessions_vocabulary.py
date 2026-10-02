"""The sessions vocabulary (§20.9, §32, §33): the document, the mount
rules, the listing every reader shares, the lineage read back from a
turn, and the handoff baton."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from neosian import ToolResult
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.sessions import (
    SESSIONS_PREFIX,
    continued_ids,
    handoff_declared,
    handoff_document,
    handoff_tier,
    parse_handoff,
    parse_sessions_document,
    project_mount,
    session_ids,
    sessions_document,
    sessions_path,
)
from neosian._foundation.shared.types import ToolCallId, ToolName

SESSION = "cc-7f3a"
SCOPE = "user:me/proj:d"
WRITTEN = datetime(2026, 10, 2, 10, 14, tzinfo=UTC)


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

    def test_continues_lines_round_trip(self) -> None:
        text = sessions_document(
            agent="opencode",
            session_id="ses_1",
            started=WRITTEN,
            last_prompt="continue",
            turns=1,
            continues=("cc-7f3a", "cc-0001"),
        )
        assert text.endswith("- turns: 1\n- continues: cc-7f3a\n- continues: cc-0001\n")
        lines = parse_sessions_document(text)
        assert lines.agent == "opencode" and lines.conversation == "ses_1"
        assert lines.continues == ("cc-7f3a", "cc-0001")

    def test_a_stranger_document_reads_as_nothing(self) -> None:
        lines = parse_sessions_document("# notes\n\n- topic: x\n")
        assert lines.agent is None and lines.conversation is None
        assert lines.continues == () and parse_sessions_document("") == lines


def _round(name: str, result: str, *, call: str = "c1") -> list[Message]:
    made = ToolCall(id=ToolCallId(call), name=ToolName(name), arguments={})
    return [
        Message(role=Role.ASSISTANT, content=None, tool_calls=[made]),
        Message(role=Role.TOOL, content=result, tool_call_id=made.id),
    ]


_HEADER = "[continuing conversation cc-7f3a — written by claude-code:cc-7f3a, 3 turns]"


class TestContinuedIds:
    def test_each_client_shape_of_the_result_is_read(self) -> None:
        raw = _round("neosian-memory_continue_session", _HEADER + "\n[1] USER: hi")
        dumped = _round(
            "mcp__neosian-memory__continue_session",
            json.dumps([{"type": "text", "text": _HEADER}], sort_keys=True),
        )
        envelope = _round("continue_session", ToolResult.ok(_HEADER).to_json())
        for messages in (raw, dumped, envelope):
            assert continued_ids(messages) == ("cc-7f3a",)

    def test_the_first_header_wins_and_several_calls_keep_their_order(self) -> None:
        quoting = _HEADER + "\n[1] TOOL recall_turn → [continuing conversation older"
        messages = [
            Message(role=Role.USER, content="continue both"),
            *_round("continue_session", quoting, call="c1"),
            *_round("continue_session", "[continuing conversation cc-0001", call="c2"),
            *_round("continue_session", "[continuing conversation cc-7f3a", call="c3"),
        ]
        assert continued_ids(messages) == ("cc-7f3a", "cc-0001")

    def test_a_stranger_quoting_the_header_or_a_failed_call_never_counts(
        self,
    ) -> None:
        quoted = _round("recall_turn", f"Turn 1 (verbatim):\nTOOL x → {_HEADER}")
        failed = _round(
            "continue_session", ToolResult.fail("nothing to continue").to_json()
        )
        assert continued_ids(quoted) == () and continued_ids(failed) == ()
        assert continued_ids([Message(role=Role.USER, content="hi")]) == ()


class TestHandoffDeclared:
    def test_the_acknowledged_call_under_any_client_name(self) -> None:
        ack = "[handoff note recorded — the next session sees it at start]"
        assert handoff_declared(_round("mcp__neosian-memory__handoff", ack))
        assert handoff_declared(_round("handoff", ToolResult.ok(ack).to_json()))
        assert not handoff_declared(_round("handoff", "[note too long]"))
        assert not handoff_declared(_round("memory", ack))


class TestHandoff:
    def test_the_baton_round_trips_pending_then_picked_up(self) -> None:
        text = handoff_document(
            actor="claude-code:cc-7f3a",
            written=WRITTEN,
            note="Next: run the gates.\n\n- status: of the build is green\n",
        )
        assert text.startswith("# handoff from claude-code:cc-7f3a\n\n- from: ")
        baton = parse_handoff(text)
        assert baton is not None and baton.pending and baton.conversation is None
        assert baton.actor == "claude-code:cc-7f3a" and baton.written == WRITTEN
        assert baton.note == "Next: run the gates.\n\n- status: of the build is green"
        picked = parse_handoff(
            handoff_document(
                actor=baton.actor,
                written=baton.written,
                note=baton.note,
                conversation="cc-7f3a",
                picked_up=(WRITTEN + timedelta(hours=1), "mcp:opencode"),
            )
        )
        assert picked is not None and not picked.pending
        assert picked.conversation == "cc-7f3a"

    def test_a_hand_edit_that_is_not_a_baton_reads_as_none(self) -> None:
        assert parse_handoff("# handoff\n\nsome prose\n") is None
        assert (
            parse_handoff("# x\n\n- from: a\n- written: not a date\n- status: p")
            is None
        )
        naive = "# x\n\n- from: a\n- written: 2026-10-02T10:14:00\n- status: pending\n"
        assert parse_handoff(naive) is None

    def test_the_tiers(self) -> None:
        pending = parse_handoff(handoff_document(actor="a", written=WRITTEN, note="n"))
        assert pending is not None
        assert handoff_tier(pending, WRITTEN + timedelta(days=6)) == "full"
        assert handoff_tier(pending, WRITTEN + timedelta(days=8)) == "line"
        picked = parse_handoff(
            handoff_document(
                actor="a", written=WRITTEN, note="n", picked_up=(WRITTEN, "b")
            )
        )
        assert picked is not None and handoff_tier(picked, WRITTEN) is None


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
