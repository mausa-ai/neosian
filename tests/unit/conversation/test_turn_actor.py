"""Turn-ref actors (NP, ledger #100): `<conversation_id>#<turn>`.

The actor closure resolves per memory command under the send lock — no
per-send agent rebuild — so version rows carry the turn each write
belongs to. Reflection's bare-conversation_id actor is pinned where
reflection lives (`test_reflection.py`); this suite pins the in-run
side: numbering, resume continuation, and the failed-send reuse rule.
"""

from typing import Any

import pytest

from neosian import AgentConfig, Model, NeosianError
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.types import SystemPrompt, ToolCallId, ToolName

_SYSTEM = SystemPrompt("You are a test agent.")


def _config(script: FakeScript, **kwargs: Any) -> AgentConfig:
    fake = FakeClient(script)
    return AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        **kwargs,
    )


def _write(path: str, content: str) -> FakeTurn:
    return FakeTurn(
        tool_calls=(
            ToolCall(
                id=ToolCallId("c1"),
                name=ToolName("memory"),
                arguments={"command": "create", "path": path, "content": content},
            ),
        )
    )


class TestTurnRefActors:
    async def test_each_send_stamps_its_turn(self, store: FileStore) -> None:
        script = FakeScript(
            turns=(
                _write("/memories/one", "1"),
                FakeTurn(content="ok"),
                _write("/memories/two", "2"),
                FakeTurn(content="ok"),
            )
        )
        convo = Conversation(
            _config(script),
            store=store,
            conversation_id="t1",
            memory_scope="user:demo",
        )
        await convo.send("first")
        await convo.send("second")
        (one,) = await store.versions("user:demo", "one")
        (two,) = await store.versions("user:demo", "two")
        assert one.actor == "t1#1"
        assert two.actor == "t1#2"

    async def test_resume_continues_the_numbering(self, store: FileStore) -> None:
        first = Conversation(
            _config(FakeScript(turns=(FakeTurn(content="hello"),))),
            store=store,
            conversation_id="t1",
            memory_scope="user:demo",
        )
        await first.send("chat only")

        script = FakeScript(
            turns=(_write("/memories/later", "x"), FakeTurn(content="ok"))
        )
        resumed = Conversation(
            _config(script),
            store=store,
            conversation_id="t1",
            memory_scope="user:demo",
        )
        await resumed.send("now write")
        (row,) = await store.versions("user:demo", "later")
        assert row.actor == "t1#2"

    async def test_failed_send_reuses_the_number(self, store: FileStore) -> None:
        """A raising send persists no turn (§9.5) — the next send takes
        the same number: a turn that never happened leaves no rows."""
        script = FakeScript(
            turns=(
                FakeTurn(error=ValueError("boom")),
                _write("/memories/after", "x"),
                FakeTurn(content="ok"),
            )
        )
        convo = Conversation(
            _config(script),
            store=store,
            conversation_id="t1",
            memory_scope="user:demo",
        )
        with pytest.raises(NeosianError):
            await convo.send("this fails")
        await convo.send("this lands")
        (row,) = await store.versions("user:demo", "after")
        assert row.actor == "t1#1"
