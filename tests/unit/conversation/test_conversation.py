"""Conversation semantics — the §9.5 rulings, blocking path.

Every scripted run rides FakeClient through `client_factory`; zero keys.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from neosian import Agent, AgentConfig, Model, Tool, ToolResult
from neosian._foundation.agent.hooks import AgentHooks, TurnEvent
from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    ConversationIdInvalidError,
    ModelFailedError,
)
from neosian._foundation.shared.types import (
    GuardrailMode,
    GuardrailsConfig,
    SystemPrompt,
    ToolCallId,
    ToolName,
)

_SYSTEM = SystemPrompt("You are a test agent.")
_CORE_LOGGER = "neosian._foundation.conversation.core"


@Tool(name="echo", description="Echo the text back")
async def echo(text: str) -> ToolResult[str]:
    return ToolResult.ok(f"echo: {text}")


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


def _reply(text: str) -> FakeScript:
    return FakeScript(turns=(FakeTurn(content=text),))


class _ProbeStore(ConversationStore):
    """In-memory ConversationStore that is deliberately NOT a MemoryStore."""

    def __init__(self) -> None:
        self.turns: list[ConversationTurn] = []
        self.projections: list[ConversationProjection] = []
        self.read_calls = 0
        self.read_projection_calls = 0

    async def append_turn(
        self, conversation_id: str, messages: Sequence[Message]
    ) -> ConversationTurn:
        turn = ConversationTurn(
            conversation_id=conversation_id,
            turn=len(self.turns) + 1,
            messages=tuple(messages),
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
        self.turns.append(turn)
        return turn

    async def read_turns(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationTurn, ...]:
        del conversation_id
        self.read_calls += 1
        selected = [t for t in self.turns if t.turn > after]
        return tuple(selected if limit is None else selected[:limit])

    async def last_turn_number(self, conversation_id: str) -> int:
        del conversation_id
        return len(self.turns)

    async def append_projections(
        self, conversation_id: str, entries: Sequence[ConversationProjection]
    ) -> None:
        del conversation_id
        self.projections.extend(entries)
        self.projections.sort(key=lambda e: (e.turn, e.span))

    async def read_projections(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationProjection, ...]:
        del conversation_id
        self.read_projection_calls += 1
        selected = [e for e in self.projections if e.turn > after]
        return tuple(selected if limit is None else selected[:limit])


@pytest.mark.unit
class TestSendPersistence:
    async def test_send_appends_one_turn(self, store: FileStore) -> None:
        config, _ = _config(_reply("hello"))
        convo = Conversation(config, store=store, conversation_id="t1")
        response = await convo.send("hi")
        assert response.message.content == "hello"
        (turn,) = await store.read_turns("t1")
        assert [m.role for m in turn.messages] == [Role.USER, Role.ASSISTANT]
        assert turn.messages[0].content == "hi"
        assert convo.messages == turn.messages

    async def test_tool_round_is_persisted_whole(self, store: FileStore) -> None:
        """The CLI's lost-tool-history bug, dogfooded: intermediate tool
        messages survive the store round-trip."""
        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("echo"),
                            arguments={"text": "ping"},
                        ),
                    )
                ),
                FakeTurn(content="done"),
            )
        )
        config, _ = _config(script, tools=[echo])
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("use the tool")
        (turn,) = await store.read_turns("t1")
        assert [m.role for m in turn.messages] == [
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
        ]
        assert turn.messages[1].tool_calls[0].name == "echo"
        assert turn.messages[2].tool_call_id == "c1"

    async def test_next_send_replays_history(self, store: FileStore) -> None:
        script = FakeScript(turns=(FakeTurn(content="one"), FakeTurn(content="two")))
        config, fake = _config(script)
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("first")
        await convo.send("second")
        replayed = [m for m in fake.calls[-1].messages if m.role is not Role.SYSTEM]
        assert [m.content for m in replayed] == ["first", "one", "second"]

    async def test_resume_rebuilds_history_verbatim(self, store: FileStore) -> None:
        config, _ = _config(_reply("one"))
        first = Conversation(config, store=store, conversation_id="t1")
        await first.send("hello")

        config_two, fake_two = _config(_reply("two"))
        resumed = Conversation(config_two, store=store, conversation_id="t1")
        await resumed.start()
        assert [m.content for m in resumed.messages] == ["hello", "one"]
        await resumed.send("again")
        replayed = [m for m in fake_two.calls[-1].messages if m.role is not Role.SYSTEM]
        assert [m.content for m in replayed] == ["hello", "one", "again"]

    async def test_failed_send_persists_nothing(self, store: FileStore) -> None:
        config, _ = _config(FakeScript(turns=(FakeTurn(error=RuntimeError("boom")),)))
        convo = Conversation(config, store=store, conversation_id="t1")
        with pytest.raises(ModelFailedError):
            await convo.send("hi")
        assert await store.read_turns("t1") == ()
        assert convo.messages == ()

    async def test_blocked_send_persists_nothing(self, store: FileStore) -> None:
        guardrail_client = AsyncMock()
        guardrail_client.chat.completions.create.return_value = AsyncMock(
            choices=[
                AsyncMock(
                    message=AsyncMock(
                        content='{"violation": 1, "category": "P1",'
                        ' "rationale": "flagged"}'
                    )
                )
            ]
        )
        config, _ = _config(
            _reply("never"),
            guardrails=GuardrailsConfig(
                input_mode=GuardrailMode.POLICY_ONLY,
                input_policy="no bad content",
                block_on_input=True,
            ),
        )
        with patch(
            "neosian._foundation.agent.base.create_guardrail_client",
            return_value=guardrail_client,
        ):
            convo = Conversation(config, store=store, conversation_id="t1")
            response = await convo.send("bad content")
        assert response.blocked is True
        assert response.turn_messages == ()
        assert await store.read_turns("t1") == ()
        assert convo.messages == ()

    async def test_concurrent_sends_are_serialized(self, store: FileStore) -> None:
        script = FakeScript(turns=(FakeTurn(content="one"), FakeTurn(content="two")))
        config, _ = _config(script)
        convo = Conversation(config, store=store, conversation_id="t1")
        await asyncio.gather(convo.send("a"), convo.send("b"))
        turns = await store.read_turns("t1")
        assert [t.turn for t in turns] == [1, 2]
        assert len(convo.messages) == 4

    async def test_send_rejects_a_non_user_message(self, store: FileStore) -> None:
        config, _ = _config(_reply("x"))
        convo = Conversation(config, store=store, conversation_id="t1")
        with pytest.raises(ValueError, match="USER message"):
            await convo.send(Message(role=Role.ASSISTANT, content="nope"))


@pytest.mark.unit
class TestConstruction:
    async def test_construction_does_no_io(self) -> None:
        probe = _ProbeStore()
        config, _ = _config(_reply("x"))
        convo = Conversation(config, store=probe, conversation_id="t1")
        assert probe.read_calls == 0
        await convo.start()
        assert probe.read_calls == 1
        await convo.start()
        assert probe.read_calls == 1  # idempotent

    def test_invalid_conversation_id_raises_at_construction(self) -> None:
        config, _ = _config(_reply("x"))
        with pytest.raises(ConversationIdInvalidError):
            Conversation(config, store=_ProbeStore(), conversation_id="a/b")

    def test_conflicting_memory_arguments_raise(self, store: FileStore) -> None:
        config, _ = _config(_reply("x"))
        memory = MemoryConfig(
            store=store, mounts=(Mount(scope="user:x", mount_path="user"),)
        )
        with pytest.raises(ConfigurationError, match="At most one"):
            Conversation(
                config,
                store=store,
                conversation_id="t1",
                memory=memory,
                memory_scope="user:x",
            )

    def test_mounts_without_a_memory_store_raise(self) -> None:
        config, _ = _config(_reply("x"))
        with pytest.raises(ConfigurationError, match="MemoryStore"):
            Conversation(
                config,
                store=_ProbeStore(),
                conversation_id="t1",
                memory_scope="user:x",
            )

    async def test_accepts_a_built_agent(self, store: FileStore) -> None:
        config, _ = _config(_reply("from agent"))
        agent = Agent(config, max_tool_iterations=7)
        convo = Conversation(agent, store=store, conversation_id="t1")
        response = await convo.send("hi")
        assert response.message.content == "from agent"
        assert convo._agent is not None
        assert convo._agent is not agent
        assert convo._agent.max_tool_iterations == 7


@pytest.mark.unit
class TestMemoryWiring:
    async def test_memory_scope_sugar_mounts_at_memories(
        self, store: FileStore
    ) -> None:
        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("memory"),
                            arguments={
                                "command": "create",
                                "path": "/memories/prefs",
                                "content": "Prefers espresso.",
                            },
                        ),
                    )
                ),
                FakeTurn(content="noted"),
            )
        )
        config, _ = _config(script)
        convo = Conversation(
            config, store=store, conversation_id="t1", memory_scope="user:demo"
        )
        response = await convo.send("remember this")
        assert response.tool_results[0].success
        document = await store.read("user:demo", "prefs")
        assert document is not None
        assert document.content == "Prefers espresso."

    async def test_native_memory_survives_derivation(self, store: FileStore) -> None:
        """The flag rides dataclasses.replace and marks the derived tool."""
        config, _ = _config(_reply("ok"), native_memory=True)
        convo = Conversation(
            config, store=store, conversation_id="t1", memory_scope="user:demo"
        )
        await convo.start()
        assert convo._agent is not None
        assert convo._agent.config.native_memory is True
        [definition] = [d for d in convo._agent._tool_definitions if d.name == "memory"]
        assert definition.native_type == "memory_20250818"

    async def test_memory_tool_is_bound_to_the_conversation_id(
        self, store: FileStore
    ) -> None:
        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("memory"),
                            arguments={
                                "command": "create",
                                "path": "/memories/prefs",
                                "content": "x",
                            },
                        ),
                    )
                ),
                FakeTurn(content="ok"),
            )
        )
        config, _ = _config(script)
        convo = Conversation(
            config, store=store, conversation_id="t1", memory_scope="user:demo"
        )
        await convo.send("remember")
        (version,) = await store.versions("user:demo", "prefs")
        assert version.actor == "t1"

    async def test_no_duplicate_memory_tool_is_registered(
        self, store: FileStore
    ) -> None:
        """A base config carrying memory= must not yield two memory tools:
        the derived config sets memory=None and rebinds the tool itself."""
        memory = MemoryConfig(
            store=store, mounts=(Mount(scope="user:demo", mount_path="user"),)
        )
        config, _ = _config(_reply("x"), memory=memory)
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.start()
        assert convo._agent is not None
        assert [str(name) for name in convo._agent._tools] == ["memory"]

    async def test_memory_index_is_frozen_per_conversation(
        self, store: FileStore
    ) -> None:
        await store.write("user:demo", "before", "existing fact")
        config, _ = _config(FakeScript(turns=(FakeTurn(content="a"),) * 2))
        convo = Conversation(
            config, store=store, conversation_id="t1", memory_scope="user:demo"
        )
        await convo.send("one")
        assert convo._agent is not None
        prompt = convo._agent._system_prompt
        assert "/memories/before" in prompt
        agent_ref = convo._agent
        await store.write("user:demo", "later", "new fact")
        await convo.send("two")
        assert convo._agent is agent_ref
        assert convo._agent._system_prompt == prompt
        assert "/memories/later" not in prompt

    async def test_no_memory_means_no_section(self, store: FileStore) -> None:
        config, _ = _config(_reply("x"))
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.start()
        assert convo._agent is not None
        assert convo._agent._system_prompt == _SYSTEM


@pytest.mark.unit
class TestCallerIsolation:
    async def test_user_hooks_still_fire(self, store: FileStore) -> None:
        seen: list[TurnEvent] = []
        config, _ = _config(_reply("x"), hooks=AgentHooks(on_turn=seen.append))
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("hi")
        assert len(seen) == 1
        assert seen[0].response.message.content == "x"
        assert len(await store.read_turns("t1")) == 1

    async def test_user_config_and_agent_are_untouched(self, store: FileStore) -> None:
        memory = MemoryConfig(
            store=store, mounts=(Mount(scope="user:demo", mount_path="user"),)
        )
        config, _ = _config(_reply("x"), tools=[echo], memory=memory)
        agent = Agent(config)
        before_tools = list(agent._tools)
        convo = Conversation(agent, store=store, conversation_id="t1")
        await convo.send("hi")
        assert config.tools == [echo]
        assert config.memory is memory
        assert config.hooks is None
        assert config.system_prompt == _SYSTEM
        assert list(agent._tools) == before_tools


def _small_config(script: FakeScript, **kwargs: Any) -> tuple[AgentConfig, FakeClient]:
    """FAKE_SMALL's 8_192-token window is the trigger-test lever."""
    fake = FakeClient(script)
    config = AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE_SMALL,
        enable_todo=False,
        client_factory=lambda _: fake,
        **kwargs,
    )
    return config, fake


class _FailingProjectionsStore(FileStore):
    async def append_projections(self, conversation_id: str, entries: Any) -> None:
        del conversation_id, entries
        raise RuntimeError("projections append failed")


# ~82-token threshold on FAKE_SMALL: any send past the first crosses it
# with 600-char user messages, so the boundary timing is deterministic.
_TRIGGER = CompactionConfig(hot_turns=1, trigger_fraction=0.01)


def _long(n: int) -> str:
    # Longer than the default user_chars cap (800), so the log line
    # head-clips and the TAIL marker survives only in verbatim turns.
    return "x" * 900 + f" TAIL{n}"


@pytest.mark.unit
class TestCompactionIntegration:
    async def test_default_on_reads_projections_once(self) -> None:
        probe = _ProbeStore()
        config, _ = _config(_reply("x"))
        convo = Conversation(config, store=probe, conversation_id="t1")
        await convo.start()
        assert probe.read_calls == 1
        assert probe.read_projection_calls == 1

    async def test_disabled_skips_the_projections_read(self) -> None:
        probe = _ProbeStore()
        config, _ = _config(_reply("x"))
        convo = Conversation(
            config,
            store=probe,
            conversation_id="t1",
            compaction=CompactionConfig(enabled=False),
        )
        await convo.start()
        assert probe.read_calls == 1
        assert probe.read_projection_calls == 0

    async def test_trigger_replaces_old_turns_with_the_log_block(
        self, store: FileStore
    ) -> None:
        script = FakeScript(turns=tuple(FakeTurn(content=f"r{i}") for i in range(3)))
        config, fake = _small_config(script)
        convo = Conversation(
            config, store=store, conversation_id="t1", compaction=_TRIGGER
        )
        await convo.send(_long(1))
        await convo.send(_long(2))
        await convo.send(_long(3))  # boundary fires here: turn 1 projected
        contents = [
            m.content for m in fake.calls[-1].messages if isinstance(m.content, str)
        ]
        assert any("[conversation log" in c for c in contents)
        assert any("[recall_turn(1)]" in c for c in contents)
        assert not any("TAIL1" in c for c in contents)  # clipped away
        assert any("TAIL2" in c for c in contents)  # hot turn verbatim
        stored = await store.read_projections("t1")
        assert [e.turn for e in stored] == [1]

    async def test_below_the_trigger_requests_are_unchanged(
        self, store: FileStore
    ) -> None:
        script = FakeScript(turns=(FakeTurn(content="one"), FakeTurn(content="two")))
        config, fake = _config(script)  # Model.FAKE: the window never triggers
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("first")
        await convo.send("second")
        replayed = [m for m in fake.calls[-1].messages if m.role is not Role.SYSTEM]
        assert [m.content for m in replayed] == ["first", "one", "second"]
        assert await store.read_projections("t1") == ()

    async def test_compaction_spend_folds_into_the_response(
        self, store: FileStore
    ) -> None:
        from neosian._foundation.conversation.distill import DigestBatch, DigestLine
        from neosian._foundation.llm.base import ModelUsage, Usage

        digest = DigestBatch(lines=[DigestLine(turn=1, line="the gist")])
        script = FakeScript(
            turns=(
                FakeTurn(content="r" * 300, usage=Usage(10, 2)),
                FakeTurn(content="r2", usage=Usage(10, 2)),
                # send 3's boundary distills turn 1, then the agent runs:
                FakeTurn(content=digest.model_dump_json(), usage=Usage(7, 3)),
                FakeTurn(content="r3", usage=Usage(50, 5)),
            )
        )
        config, _ = _small_config(script)
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(
                hot_turns=1, trigger_fraction=0.01, digest_chars=100
            ),
        )
        await convo.send(_long(1))
        await convo.send(_long(2))
        response = await convo.send(_long(3))
        assert response.usage == Usage(57, 8)
        assert response.usage_by_model == (
            ModelUsage(model="fake-small", usage=Usage(57, 8)),
        )
        stored = await store.read_projections("t1")
        assert stored[0].kind == "digest"
        assert "the gist" in stored[0].text

    async def test_memory_index_refreshes_at_the_boundary(
        self, store: FileStore
    ) -> None:
        """The §9.5.10 carve-out: the frozen index's one legitimate
        refresh — the sibling of test_memory_index_is_frozen_per_conversation."""
        await store.write("user:demo", "before", "existing fact")
        config, _ = _config(FakeScript(turns=(FakeTurn(content="a"),) * 2))
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            memory_scope="user:demo",
            compaction=CompactionConfig(hot_turns=1),
        )
        await convo.send("one")
        await convo.send("two")
        assert convo._agent is not None
        agent_ref = convo._agent
        await store.write("user:demo", "later", "new fact")
        result = await convo.compact()
        assert result.entries
        assert convo._agent is not agent_ref
        assert "/memories/later" in convo._agent._system_prompt

    async def test_resume_renders_compacted_from_the_first_send(
        self, store: FileStore
    ) -> None:
        config, _ = _config(FakeScript(turns=(FakeTurn(content="a"),) * 2))
        first = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await first.send("one")
        await first.send("two")
        await first.compact()

        config2, fake2 = _config(_reply("b"))
        resumed = Conversation(
            config2,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await resumed.send("three")
        contents = [
            m.content for m in fake2.calls[-1].messages if isinstance(m.content, str)
        ]
        assert any("[conversation log" in c and "[1] USER: one" in c for c in contents)

    async def test_projections_failure_fails_the_send_and_persists_nothing(
        self, tmp_path: Any, manual_clock: Any
    ) -> None:
        store = _FailingProjectionsStore(tmp_path / "failing", clock=manual_clock)
        script = FakeScript(turns=tuple(FakeTurn(content=f"r{i}") for i in range(3)))
        config, fake = _small_config(script)
        convo = Conversation(
            config, store=store, conversation_id="t1", compaction=_TRIGGER
        )
        await convo.send(_long(1))
        await convo.send(_long(2))
        calls_before = len(fake.calls)
        with pytest.raises(RuntimeError, match="projections append failed"):
            await convo.send(_long(3))
        assert await store.last_turn_number("t1") == 2
        assert len(convo.messages) == 4  # two persisted turns, nothing half-done
        assert len(fake.calls) == calls_before  # the agent never ran

    async def test_caller_config_is_untouched_by_compaction(
        self, store: FileStore
    ) -> None:
        config, _ = _config(FakeScript(turns=(FakeTurn(content="a"),) * 2))
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await convo.send("one")
        await convo.send("two")
        await convo.compact()
        assert config.tools == []
        assert config.system_prompt == _SYSTEM


@pytest.mark.unit
class TestManualCompact:
    async def test_projects_below_the_trigger(self, store: FileStore) -> None:
        config, fake = _config(FakeScript(turns=(FakeTurn(content="a"),) * 3))
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1),
        )
        await convo.send("one")
        await convo.send("two")
        result = await convo.compact()
        assert [e.turn for e in result.entries] == [1]
        assert result.usage is None  # deterministic-only: no model call
        await convo.send("three")
        contents = [
            m.content for m in fake.calls[-1].messages if isinstance(m.content, str)
        ]
        assert any("[conversation log" in c for c in contents)

    async def test_runs_even_when_disabled(self, store: FileStore) -> None:
        config, fake = _config(FakeScript(turns=(FakeTurn(content="a"),) * 3))
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(enabled=False, hot_turns=1),
        )
        await convo.send("one")
        await convo.send("two")
        result = await convo.compact()
        assert [e.turn for e in result.entries] == [1]
        await convo.send("three")
        contents = [
            m.content for m in fake.calls[-1].messages if isinstance(m.content, str)
        ]
        assert any("[conversation log" in c for c in contents)

    async def test_no_op_on_a_fresh_conversation(self, store: FileStore) -> None:
        config, _ = _config(_reply("a"))
        convo = Conversation(config, store=store, conversation_id="t1")
        result = await convo.compact()
        assert result.entries == ()
        assert result.usage is None
        assert await store.read_projections("t1") == ()


@pytest.mark.unit
class TestServerCompactionWarning:
    def test_flag_under_a_conversation_warns(
        self, store: FileStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log-projection drops server compaction blocks at the warm
        boundary — the server would re-compact and re-bill every send."""
        config, _ = _config(_reply("ok"), server_compaction=True)
        with caplog.at_level(logging.WARNING, logger=_CORE_LOGGER):
            Conversation(config, store=store, conversation_id="t1")
        assert any("server_compaction" in r.message for r in caplog.records)

    def test_flag_off_stays_silent(
        self, store: FileStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        config, _ = _config(_reply("ok"))
        with caplog.at_level(logging.WARNING, logger=_CORE_LOGGER):
            Conversation(config, store=store, conversation_id="t1")
        assert not [r for r in caplog.records if r.name == _CORE_LOGGER]
