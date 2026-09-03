"""Conversation views (§21, ledger #137–#139): another conversation as a
frozen, fully log-projected, read-only block that never spends; the
`recall_turn` tool addressing it by `conversation=`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian import AgentConfig, Model
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.recall import create_recall_turn_tool
from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.conversation.views import (
    ConversationView,
    project_conversation,
)
from neosian._foundation.llm.base import Message, Role, ToolCall, text_of
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.app import build_app
from neosian._foundation.server.remote import RemoteStore
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    ConversationIdInvalidError,
)
from neosian._foundation.shared.types import SystemPrompt, ToolCallId, ToolName


def _config(*turns: FakeTurn, **kwargs: Any) -> tuple[AgentConfig, FakeClient]:
    fake = FakeClient(FakeScript(turns=turns))
    config = AgentConfig(
        system_prompt=SystemPrompt("You are a test agent."),
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        **kwargs,
    )
    return config, fake


async def _seed(store: FileStore, conversation_id: str, *texts: str) -> None:
    for text in texts:
        await store.append_turn(
            conversation_id,
            (
                Message(role=Role.USER, content=text),
                Message(role=Role.ASSISTANT, content=f"re: {text}"),
            ),
        )


def _messages(fake: FakeClient, call: int = -1) -> list[str]:
    return [text_of(m) for m in fake.calls[call].messages]


def _tool_names(fake: FakeClient, call: int = -1) -> list[str]:
    return [str(tool.name) for tool in fake.calls[call].tools]


def _recall(turn: int, conversation: str | None) -> FakeTurn:
    arguments: dict[str, Any] = {"turn": turn}
    if conversation is not None:
        arguments["conversation"] = conversation
    return FakeTurn(
        tool_calls=(
            ToolCall(
                id=ToolCallId("r1"), name=ToolName("recall_turn"), arguments=arguments
            ),
        )
    )


@pytest.mark.unit
class TestProjectConversation:
    async def test_every_turn_is_one_line(self, store: FileStore) -> None:
        await _seed(store, "a", "one", "two")
        turns = await store.read_turns("a")
        lines = project_conversation(
            turns, (), digest_chars=200, user_chars=800, budget_chars=8192
        )
        assert lines == [
            "[1] USER: one | AGENT: re: one",
            "[2] USER: two | AGENT: re: two",
        ]

    async def test_a_projection_wins_over_the_deterministic_line(
        self, store: FileStore
    ) -> None:
        await _seed(store, "a", "one", "two", "three")
        turns = await store.read_turns("a")
        entry = ConversationProjection(turn=2, kind="epoch", text="the start", span=2)
        lines = project_conversation(
            turns, (entry,), digest_chars=200, user_chars=800, budget_chars=8192
        )
        assert lines == ["[1-2] the start", "[3] USER: three | AGENT: re: three"]

    async def test_over_budget_the_oldest_fold(self, store: FileStore) -> None:
        await _seed(store, "a", "one", "two", "three")
        turns = await store.read_turns("a")
        lines = project_conversation(
            turns, (), digest_chars=200, user_chars=800, budget_chars=40
        )
        assert lines == [
            "[1-2] 2 earlier turns not shown",
            "[3] USER: three | AGENT: re: three",
        ]


@pytest.mark.unit
class TestConstruction:
    def test_the_id_follows_the_grammar(self) -> None:
        with pytest.raises(ConversationIdInvalidError):
            ConversationView("no/slash")

    def test_the_budget_is_positive(self) -> None:
        with pytest.raises(ConfigurationError):
            ConversationView("a", budget_chars=0)

    def test_a_view_of_oneself_is_refused(self, store: FileStore) -> None:
        config, _ = _config()
        with pytest.raises(ConfigurationError, match="itself"):
            Conversation(
                config,
                store=store,
                conversation_id="a",
                context=[ConversationView("a")],
            )

    def test_a_duplicate_view_is_refused(self, store: FileStore) -> None:
        config, _ = _config()
        with pytest.raises(ConfigurationError, match="twice"):
            Conversation(
                config,
                store=store,
                conversation_id="b",
                context=[ConversationView("a"), ConversationView("a")],
            )


@pytest.mark.unit
class TestTheBlock:
    async def test_rendered_before_own_history(self, store: FileStore) -> None:
        await _seed(store, "a", "one", "two")
        config, fake = _config(FakeTurn(content="ok"))
        convo = Conversation(
            config, store=store, conversation_id="b", context=[ConversationView("a")]
        )
        await convo.send("what did A do?")
        system, block, user = _messages(fake)
        assert system.startswith("You are a test agent.")
        assert block.startswith("[view of conversation a")
        assert "[1] USER: one | AGENT: re: one" in block
        assert "[2] USER: two | AGENT: re: two" in block
        assert block.endswith(
            'recall_turn(n, conversation="a") to re-read its turn n verbatim]'
        )
        assert user == "what did A do?"
        assert fake.calls[-1].messages[1].role is Role.USER

    async def test_an_empty_source_still_shows_its_frame(
        self, store: FileStore
    ) -> None:
        config, fake = _config(FakeTurn(content="ok"))
        convo = Conversation(
            config, store=store, conversation_id="b", context=[ConversationView("a")]
        )
        await convo.send("hi")
        assert "(no turns yet)" in _messages(fake)[1]

    async def test_frozen_per_instance_refreshed_at_the_boundary(
        self, store: FileStore
    ) -> None:
        await _seed(store, "a", "one")
        config, fake = _config(
            FakeTurn(content="r1"), FakeTurn(content="r2"), FakeTurn(content="r3")
        )
        convo = Conversation(
            config,
            store=store,
            conversation_id="b",
            context=[ConversationView("a")],
            compaction=CompactionConfig(hot_turns=1),
        )
        await convo.send("first")
        await _seed(store, "a", "two")  # A moves on after B started
        await convo.send("second")
        assert "[2] USER: two" not in _messages(fake)[1]
        result = await convo.compact()
        assert result.entries
        await convo.send("third")
        assert "[2] USER: two" in _messages(fake)[1]

    async def test_the_views_do_not_persist(self, store: FileStore) -> None:
        await _seed(store, "a", "one")
        config, _ = _config(FakeTurn(content="ok"))
        convo = Conversation(
            config, store=store, conversation_id="b", context=[ConversationView("a")]
        )
        await convo.send("hi")
        (turn,) = await store.read_turns("b")
        assert [m.role for m in turn.messages] == [Role.USER, Role.ASSISTANT]


@pytest.mark.unit
class TestCrossConversationRecall:
    async def test_registered_from_the_first_send(self, store: FileStore) -> None:
        config, fake = _config(FakeTurn(content="ok"))
        convo = Conversation(
            config, store=store, conversation_id="b", context=[ConversationView("a")]
        )
        await convo.send("hi")
        assert "recall_turn" in _tool_names(fake)

    async def test_recalls_the_viewed_turn_verbatim(self, store: FileStore) -> None:
        await _seed(store, "a", "the parser is in src/parse.py")
        config, fake = _config(_recall(1, "a"), FakeTurn(content="got it"))
        convo = Conversation(
            config, store=store, conversation_id="b", context=[ConversationView("a")]
        )
        await convo.send("where is the parser?")
        assert "USER: the parser is in src/parse.py" in _messages(fake, 1)[-1]

    async def test_a_stranger_fails_correctively(self, store: FileStore) -> None:
        tool = create_recall_turn_tool(store, "b", addressable=["a"])
        result = await tool(turn=1, conversation="zzz")
        assert not result.success
        assert result.system_reminder == (
            "recall_turn reaches this conversation and the ones shown as "
            "views: 'b', 'a'."
        )

    async def test_out_of_range_names_the_foreign_conversation(
        self, store: FileStore
    ) -> None:
        await _seed(store, "a", "one")
        tool = create_recall_turn_tool(store, "b", addressable=["a"])
        result = await tool(turn=5, conversation="a")
        assert not result.success
        assert result.system_reminder == "Conversation 'a' has turns 1-1."

    async def test_none_is_the_own_conversation(self, store: FileStore) -> None:
        await _seed(store, "b", "mine")
        tool = create_recall_turn_tool(store, "b", addressable=["a"])
        result = await tool(turn=1)
        assert result.success and result.data is not None
        assert "USER: mine" in result.data


@pytest.mark.unit
class TestThroughTheDaemon:
    async def test_a_view_over_the_wire(self, tmp_path: Path) -> None:
        backing = FileStore(tmp_path / "backing")
        await _seed(backing, "a", "one")
        app = await build_app(backing, token="t")
        remote = await RemoteStore.connect(
            "http://state-process", token="t", transport=httpx.ASGITransport(app=app)
        )
        config, fake = _config(_recall(1, "a"), FakeTurn(content="ok"))
        convo = Conversation(
            config, store=remote, conversation_id="b", context=[ConversationView("a")]
        )
        await convo.send("hi")
        assert "[1] USER: one | AGENT: re: one" in _messages(fake, 0)[1]
        assert "USER: one" in _messages(fake, 1)[-1]
        await remote.aclose()
