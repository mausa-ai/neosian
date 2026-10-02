"""The handoff (DESIGN §33): what `continue_session` delivers and picks
up, what `handoff` writes, over FileStore with a clock the test holds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from neosian._foundation.conversation.handoff import (
    CONTINUE_BUDGET_CHARS,
    handoff_tools,
    render_continuation,
)
from neosian._foundation.conversation.recall import static_reach
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.sessions import (
    HANDOFF_NOTE_CHARS,
    HANDOFF_PATH,
    parse_handoff,
    sessions_document,
    sessions_path,
)
from neosian._foundation.memory.types import MemoryDocument
from neosian._foundation.shared.exceptions import MemoryConflictError
from neosian._foundation.shared.types import ToolCallId, ToolName

SCOPE = "user:me/proj:d"
START = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


class Clock:
    """A clock the test moves: every store write and baton stamp reads it."""

    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at


def _call(name: str = "Bash", **arguments: Any) -> ToolCall:
    return ToolCall(id=ToolCallId("c1"), name=ToolName(name), arguments=arguments)


async def _session(
    store: FileStore, cid: str, *prompts: str, agent: str = "cc"
) -> None:
    """`prompts` turns of `cid` (one tool round in each), then its sessions
    document, the way the record verb lands them."""
    for prompt in prompts:
        await store.append_turn(
            cid,
            (
                Message(role=Role.USER, content=prompt),
                Message(role=Role.ASSISTANT, content="looking", tool_calls=[_call()]),
                Message(
                    role=Role.TOOL, content="x" * 300, tool_call_id=ToolCallId("c1")
                ),
                Message(role=Role.ASSISTANT, content=f"done: {prompt}"),
            ),
            actor=f"{agent}:{cid}",
        )
    turns = await store.read_turns(cid)
    await store.write(
        SCOPE,
        sessions_path(cid),
        sessions_document(
            agent=agent,
            session_id=cid,
            started=turns[0].created_at,
            last_prompt=prompts[-1],
            turns=len(prompts),
        ),
        actor=f"{agent}:{cid}#{len(prompts)}",
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> FileStore:
    return FileStore(tmp_path / "home", clock=clock)


def _tools(store: FileStore, clock: Clock, **kwargs: Any) -> tuple[Any, Any]:
    return handoff_tools(store, store, SCOPE, actor="mcp:test", clock=clock, **kwargs)


@pytest.mark.unit
class TestRenderContinuation:
    async def test_verbatim_prompts_and_answers_tool_rounds_one_line(
        self, store: FileStore
    ) -> None:
        await _session(store, "a", "first " * 50, "second")
        turns = await store.read_turns("a")
        text = render_continuation(turns, (), continues=("z",))
        assert text.startswith(
            "[continuing conversation a — written by cc:a, 2 turns, "
        )
        assert ", itself continuing z;" in text
        assert "[1] USER: " + "first " * 50 in text
        assert "AGENT: looking | " not in text and "AGENT: looking\n" in text
        assert text.count("TOOL Bash({}) → ") == 2 and "x" * 300 not in text
        assert "AGENT: done: second\n[end — you are continuing" in text

    async def test_over_budget_the_oldest_turns_become_log_lines(
        self, store: FileStore
    ) -> None:
        await _session(store, "a", *(f"prompt {i} " * 40 for i in range(12)))
        turns = await store.read_turns("a")
        text = render_continuation(turns, (), budget_chars=3000)
        assert "[12] USER: " in text and "[1] USER: " not in text
        assert "[1-" in text  # the fold line of the oldest
        whole = render_continuation(turns, (), budget_chars=CONTINUE_BUDGET_CHARS)
        assert "[1] USER: " in whole and "[12] USER: " in whole


@pytest.mark.unit
class TestContinueSession:
    async def test_the_default_is_the_newest_listed_session_not_own(
        self, store: FileStore, clock: Clock
    ) -> None:
        await _session(store, "older", "one")
        await _session(store, "newer", "two")
        await _session(store, "mine", "three")
        continuing, _ = _tools(store, clock, own="mine")
        result = await continuing()
        assert result.success and result.data is not None
        assert result.data.startswith("[continuing conversation newer —")
        named = await continuing(conversation="older")
        assert named.data is not None and "[1] USER: one" in named.data

    async def test_nothing_listed_or_no_turns_is_corrective(
        self, store: FileStore, clock: Clock
    ) -> None:
        continuing, _ = _tools(store, clock)
        empty = await continuing()
        assert not empty.success and empty.error is not None
        assert empty.error.startswith("Nothing to continue")
        await store.write(SCOPE, sessions_path("ghost"), "# listed, no turns")
        assert (await continuing()).error == "Conversation 'ghost' has no turns"
        reached, _ = _tools(store, clock, reach=static_reach(("a",)), where="here")
        stranger = await reached(conversation="b")
        assert not stranger.success and "not addressable" in str(stranger.error)

    async def test_a_linked_note_is_delivered_and_picked_up_once(
        self, store: FileStore, clock: Clock
    ) -> None:
        await _session(store, "depart", "build it")
        _, departing = _tools(store, clock, own="depart")
        ack = await departing(note="Next: run the gates.")
        assert ack.success and str(ack.data).startswith("[handoff note recorded")
        await _session(store, "later", "unrelated")  # newer, and not the note's
        continuing, _ = _tools(store, clock)
        result = await continuing()
        assert result.data is not None
        assert result.data.startswith("[continuing conversation depart —")
        assert (
            "[its handoff note, written 2026-10-03T12:00Z by mcp:test]" in result.data
        )
        assert "\nNext: run the gates.\n[end of note]\n" in result.data
        baton = await store.read(SCOPE, HANDOFF_PATH)
        assert baton is not None and baton.version == 2
        picked = parse_handoff(baton.content)
        assert picked is not None and not picked.pending
        assert picked.conversation == "depart" and "by mcp:test" in baton.content
        again = await continuing()
        assert again.data is not None and "handoff note" not in again.data
        assert again.data.startswith("[continuing conversation later —")

    async def test_an_unlinked_note_means_the_session_landed_after_it(
        self, store: FileStore, clock: Clock
    ) -> None:
        await _session(store, "before", "earlier work")
        _, departing = _tools(store, clock)  # no own: the server's shape
        await departing(note="pick this up")
        continuing, _ = _tools(store, clock)
        early = await continuing()
        assert not early.success
        assert early.error == "The handoff note's session has not landed yet"
        await _session(store, "depart", "the departing session")
        result = await continuing()
        assert result.data is not None
        assert result.data.startswith("[continuing conversation depart —")
        assert "pick this up" in result.data
        baton = await store.read(SCOPE, HANDOFF_PATH)
        assert baton is not None and "- conversation: depart" in baton.content

    async def test_naming_another_session_leaves_the_note_standing(
        self, store: FileStore, clock: Clock
    ) -> None:
        await _session(store, "depart", "x")
        await _session(store, "other", "y")
        _, departing = _tools(store, clock, own="depart")
        await departing(note="mine")
        continuing, _ = _tools(store, clock)
        result = await continuing(conversation="other")
        assert result.data is not None and "handoff note" not in result.data
        baton = await store.read(SCOPE, HANDOFF_PATH)
        assert baton is not None and baton.version == 1

    async def test_the_pickup_survives_one_conflicting_write(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        raced = (
            "# handoff from a\n\n- from: a\n- written: 2026-10-03T12:00:00Z\n"
            "- status: pending\n\nraced\n"
        )

        class Racing(FileStore):
            """The departing Stop rewrites the baton between the tool's
            read and its pickup write, once."""

            hit = False

            async def write(
                self,
                scope: str,
                path: str,
                content: str,
                *,
                actor: str | None = None,
                expected_version: int | None = None,
            ) -> MemoryDocument:
                if expected_version is not None and not self.hit:
                    self.hit = True
                    await FileStore.write(self, scope, path, raced)
                    raise MemoryConflictError(
                        scope,
                        path,
                        "version_mismatch",
                        expected_version=expected_version,
                        actual_version=expected_version + 1,
                    )
                return await FileStore.write(
                    self,
                    scope,
                    path,
                    content,
                    actor=actor,
                    expected_version=expected_version,
                )

        store = Racing(tmp_path / "home", clock=clock)
        await _session(store, "depart", "x")
        _, departing = _tools(store, clock, own="depart")
        await departing(note="mine")
        continuing, _ = _tools(store, clock)
        result = await continuing()
        assert result.success
        baton = await store.read(SCOPE, HANDOFF_PATH)
        assert baton is not None and baton.version == 3
        picked = parse_handoff(baton.content)
        assert picked is not None and not picked.pending and picked.note == "raced"


@pytest.mark.unit
class TestHandoff:
    async def test_the_note_is_written_once_per_scope_and_refused_when_bad(
        self, store: FileStore, clock: Clock
    ) -> None:
        _, departing = _tools(store, clock, own="depart")
        empty = await departing(note="  ")
        assert not empty.success and empty.error == "Write the note first"
        assert str(empty.system_reminder).startswith("Say what the next agent needs")
        long = await departing(note="n" * (HANDOFF_NOTE_CHARS + 1))
        assert not long.success and str(long.error).startswith("The note is 2049")
        first = await departing(note="first\n")
        second = await departing(note="second")
        assert first.success and second.success
        baton = await store.read(SCOPE, HANDOFF_PATH)
        assert baton is not None and baton.version == 2
        parsed = parse_handoff(baton.content)
        assert parsed is not None and parsed.note == "second" and parsed.pending
        assert parsed.actor == "mcp:test" and parsed.conversation == "depart"
        versions = await store.versions(SCOPE, HANDOFF_PATH)
        assert [v.actor for v in versions] == ["mcp:test", "mcp:test"]
        assert "first" in versions[-1].content
