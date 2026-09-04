"""The recall_turn tool (§9.6): verbatim re-hydration, corrective
failures, and lazy registration on the Conversation's derived agent."""

from typing import Any

import pytest

from neosian import AgentConfig, Model
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.recall import (
    create_recall_any_tool,
    create_recall_turn_tool,
)
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore

_SYSTEM = "You are a test agent."


def _config(script: FakeScript, **kwargs: Any) -> tuple[AgentConfig, FakeClient]:
    fake = FakeClient(script)
    config = AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        **kwargs,
    )
    return config, fake


def _replies(n: int) -> FakeScript:
    return FakeScript(turns=tuple(FakeTurn(content=f"reply {i}") for i in range(n)))


async def _seed(store: FileStore, texts: list[str]) -> None:
    for text in texts:
        await store.append_turn(
            "t1",
            (
                Message(role=Role.USER, content=text),
                Message(role=Role.ASSISTANT, content=f"re: {text}"),
            ),
        )


def _last_tool_names(fake: FakeClient) -> list[str]:
    return [str(tool.name) for tool in fake.calls[-1].tools]


@pytest.mark.unit
class TestRecallTool:
    async def test_returns_the_verbatim_turn(self, store: FileStore) -> None:
        await _seed(store, ["first", "second"])
        tool = create_recall_turn_tool(store, "t1")
        result = await tool(turn=2)
        assert result.success
        assert result.data is not None
        assert "Turn 2 (verbatim):" in result.data
        assert "USER: second" in result.data
        assert "AGENT: re: second" in result.data

    @pytest.mark.parametrize("turn", [0, -3])
    async def test_below_one_fails_correctively(
        self, store: FileStore, turn: int
    ) -> None:
        tool = create_recall_turn_tool(store, "t1")
        result = await tool(turn=turn)
        assert not result.success
        assert result.system_reminder is not None
        assert "square brackets" in result.system_reminder

    async def test_out_of_range_names_the_valid_span(self, store: FileStore) -> None:
        await _seed(store, ["only"])
        tool = create_recall_turn_tool(store, "t1")
        result = await tool(turn=99)
        assert not result.success
        assert result.system_reminder == "This conversation has turns 1-1."

    async def test_a_raising_store_surfaces_as_failure_not_exception(
        self, store: FileStore
    ) -> None:
        file = store._root / "conversations" / "t1" / "turns.jsonl"  # noqa: SLF001
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("not json\n", encoding="utf-8")
        tool = create_recall_turn_tool(store, "t1")
        result = await tool(turn=1)
        assert not result.success
        assert result.error is not None
        assert "[agent_conversation_format_unsupported]" in result.error


@pytest.mark.unit
class TestLazyRegistration:
    async def test_absent_before_the_first_boundary(self, store: FileStore) -> None:
        config, fake = _config(_replies(1))
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("hello")
        assert "recall_turn" not in _last_tool_names(fake)

    async def test_present_after_a_manual_boundary(self, store: FileStore) -> None:
        config, fake = _config(_replies(3))
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await convo.send("one")
        await convo.send("two")
        result = await convo.compact()
        assert result.entries
        await convo.send("three")
        assert "recall_turn" in _last_tool_names(fake)

    async def test_present_from_the_first_send_on_resume(
        self, store: FileStore
    ) -> None:
        config, _ = _config(_replies(2))
        first = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await first.send("one")
        await first.send("two")
        await first.compact()

        config2, fake2 = _config(_replies(1))
        resumed = Conversation(
            config2,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await resumed.send("three")
        assert "recall_turn" in _last_tool_names(fake2)

    async def test_works_without_memory(self, store: FileStore) -> None:
        """recall_turn rides compaction, not memory — memory=None is fine."""
        config, fake = _config(_replies(3))
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await convo.send("one")
        await convo.send("two")
        await convo.compact()
        await convo.send("what was turn 1?")
        names = _last_tool_names(fake)
        assert names == ["recall_turn"]

    async def test_recall_tool_false_never_registers(self, store: FileStore) -> None:
        config, fake = _config(_replies(3))
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1, recall_tool=False),
        )
        await convo.send("one")
        await convo.send("two")
        await convo.compact()
        await convo.send("three")
        assert "recall_turn" not in _last_tool_names(fake)


@pytest.mark.unit
class TestRecallAny:
    """The server's twin (§21.7): `conversation` required, any id the
    store holds, the same corrective failures."""

    async def test_reads_any_conversation_verbatim(self, store: FileStore) -> None:
        await _seed(store, ["first", "second"])
        tool = create_recall_any_tool(store)
        result = await tool(turn=2, conversation="t1")
        assert result.success and result.data is not None
        assert "USER: second" in result.data

    async def test_conversation_is_required(self, store: FileStore) -> None:
        tool = create_recall_any_tool(store)
        with pytest.raises(TypeError):  # tool_exec maps this to invalid arguments
            await tool(turn=1)

    async def test_out_of_range_names_the_conversation(self, store: FileStore) -> None:
        await _seed(store, ["only"])
        result = await create_recall_any_tool(store)(turn=3, conversation="t1")
        assert not result.success
        assert result.system_reminder == "Conversation 't1' has turns 1-1."

    async def test_a_bad_id_is_a_failure_never_an_exception(
        self, store: FileStore
    ) -> None:
        result = await create_recall_any_tool(store)(turn=1, conversation="a/b")
        assert not result.success and result.error is not None
        assert "conversation_id_invalid" in result.error
