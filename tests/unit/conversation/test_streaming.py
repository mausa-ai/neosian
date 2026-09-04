"""Conversation streaming parity — §9.5 rulings 3 and 7.

The persistence seam is `on_turn`, fired before the terminal event
(register #6); the streamed and blocking paths must persist identical
turns, and a consumer that saw `done` holds a persisted turn even if it
stops iterating there.
"""

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import pytest

from neosian import AgentConfig, Model
from neosian._foundation.agent.events import ContentEvent, DoneEvent
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.distill import DigestBatch, DigestLine
from neosian._foundation.llm.base import ModelUsage, Role, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore

from .conftest import ManualClock

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


def _reply(text: str) -> FakeScript:
    return FakeScript(turns=(FakeTurn(content=text),))


class _FailingAppendStore(FileStore):
    async def append_turn(
        self, conversation_id: str, messages: Any, *, actor: str | None = None
    ) -> Any:
        del conversation_id, messages, actor
        raise RuntimeError("append failed")


async def _close(events: AsyncIterator[Any]) -> None:
    assert isinstance(events, AsyncGenerator)
    await events.aclose()


@pytest.mark.unit
class TestStreamingPersistence:
    async def test_streamed_send_persists_the_same_turn_as_blocking(
        self, tmp_path: Any, manual_clock: ManualClock
    ) -> None:
        blocking_store = FileStore(tmp_path / "blocking", clock=manual_clock)
        streaming_store = FileStore(tmp_path / "streaming", clock=manual_clock)

        config_a, _ = _config(_reply("hello there"))
        blocking = Conversation(config_a, store=blocking_store, conversation_id="t1")
        await blocking.send("hi")

        config_b, _ = _config(_reply("hello there"))
        streaming = Conversation(config_b, store=streaming_store, conversation_id="t1")
        events = await streaming.send("hi", stream=True)
        async for _ in events:
            pass

        (blocked_turn,) = await blocking_store.read_turns("t1")
        (streamed_turn,) = await streaming_store.read_turns("t1")
        assert streamed_turn.messages == blocked_turn.messages
        assert streaming.messages == blocking.messages

    async def test_turn_is_persisted_before_the_done_event_is_relayed(
        self, store: FileStore
    ) -> None:
        """A consumer that stops iterating at `done` still holds a
        persisted turn (register #6 + §9.5 ruling 7)."""
        config, _ = _config(_reply("hello"))
        convo = Conversation(config, store=store, conversation_id="t1")
        events = await convo.send("hi", stream=True)
        saw_done = False
        async for event in events:
            if isinstance(event, DoneEvent):
                assert len(await store.read_turns("t1")) == 1
                saw_done = True
                break
        await _close(events)
        assert saw_done
        (turn,) = await store.read_turns("t1")
        assert [m.role for m in turn.messages] == [Role.USER, Role.ASSISTANT]

    async def test_abandoned_stream_persists_nothing(self, store: FileStore) -> None:
        config, _ = _config(_reply("a long streamed reply, many chunks"))
        convo = Conversation(config, store=store, conversation_id="t1")
        events = await convo.send("hi", stream=True)
        async for event in events:
            if isinstance(event, ContentEvent):
                break
        await _close(events)
        assert await store.read_turns("t1") == ()
        assert convo.messages == ()

    async def test_store_failure_surfaces_to_the_consumer(
        self, tmp_path: Any, manual_clock: ManualClock
    ) -> None:
        store = _FailingAppendStore(tmp_path / "failing", clock=manual_clock)
        config, _ = _config(_reply("hello"))
        convo = Conversation(config, store=store, conversation_id="t1")
        events = await convo.send("hi", stream=True)
        received: list[Any] = []
        with pytest.raises(RuntimeError, match="append failed"):
            async for event in events:
                received.append(event)
        assert not any(isinstance(e, DoneEvent) for e in received)
        assert convo.messages == ()

    async def test_streamed_history_replays_on_the_next_send(
        self, store: FileStore
    ) -> None:
        script = FakeScript(turns=(FakeTurn(content="one"), FakeTurn(content="two")))
        config, fake = _config(script)
        convo = Conversation(config, store=store, conversation_id="t1")
        events = await convo.send("first", stream=True)
        async for _ in events:
            pass
        await convo.send("second")
        replayed = [m for m in fake.calls[-1].messages if m.role is not Role.SYSTEM]
        assert [m.content for m in replayed] == ["first", "one", "second"]

    async def test_streamed_send_continues_blocking_numbering(
        self, store: FileStore
    ) -> None:
        script = FakeScript(turns=(FakeTurn(content="one"), FakeTurn(content="two")))
        config, _ = _config(script)
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("first")
        events = await convo.send("second", stream=True)
        async for _ in events:
            pass
        turns = await store.read_turns("t1")
        assert [t.turn for t in turns] == [1, 2]
        assert turns[1].messages[-1].content == "two"


def _small_config(script: FakeScript, **kwargs: Any) -> tuple[AgentConfig, FakeClient]:
    fake = FakeClient(script)
    config = AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE_SMALL,
        enable_todo=False,
        client_factory=lambda _: fake,
        **kwargs,
    )
    return config, fake


def _long(n: int) -> str:
    return "x" * 900 + f" TAIL{n}"


_TRIGGER = CompactionConfig(hot_turns=1, trigger_fraction=0.01)


@pytest.mark.unit
class TestStreamingCompaction:
    async def test_streamed_send_compacts_like_blocking(self, store: FileStore) -> None:
        script = FakeScript(turns=tuple(FakeTurn(content=f"r{i}") for i in range(3)))
        config, fake = _small_config(script)
        convo = Conversation(
            config, store=store, conversation_id="t1", compaction=_TRIGGER
        )
        await convo.send(_long(1))
        await convo.send(_long(2))
        events = await convo.send(_long(3), stream=True)
        async for _ in events:
            pass
        contents = [
            m.content for m in fake.calls[-1].messages if isinstance(m.content, str)
        ]
        assert any("[conversation log" in c for c in contents)
        assert not any("TAIL1" in c for c in contents)
        assert [e.turn for e in await store.read_projections("t1")] == [1]
        assert await store.last_turn_number("t1") == 3

    async def test_done_event_carries_the_folded_usage(self, store: FileStore) -> None:
        digest = DigestBatch(lines=[DigestLine(turn=1, line="the gist")])
        script = FakeScript(
            turns=(
                FakeTurn(content="r" * 300, usage=Usage(10, 2)),
                FakeTurn(content="r2", usage=Usage(10, 2)),
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
        events = await convo.send(_long(3), stream=True)
        done: DoneEvent | None = None
        async for event in events:
            if isinstance(event, DoneEvent):
                done = event
        assert done is not None
        assert done.usage == Usage(57, 8)
        assert done.usage_by_model == (
            ModelUsage(model="fake-small", usage=Usage(57, 8)),
        )
        payload = done.to_dict()
        assert set(payload) == {
            "event",
            "sequence",
            "model",
            "stop_reason",
            "raw_stop_reason",
            "usage",
            "usage_by_model",
            "iterations_exhausted",
        }

    async def test_abandoning_after_the_boundary_keeps_the_checkpoint(
        self, store: FileStore
    ) -> None:
        """Projections are written pre-run; the turn is not."""
        script = FakeScript(turns=tuple(FakeTurn(content=f"r{i}") for i in range(3)))
        config, _ = _small_config(script)
        convo = Conversation(
            config, store=store, conversation_id="t1", compaction=_TRIGGER
        )
        await convo.send(_long(1))
        await convo.send(_long(2))
        events = await convo.send(_long(3), stream=True)
        async for _event in events:
            break  # abandon immediately
        await _close(events)
        assert [e.turn for e in await store.read_projections("t1")] == [1]
        assert await store.last_turn_number("t1") == 2
