"""Session reuse (DESIGN §9.5.14): one client pool per Conversation.

Sends and compaction's distillation share one internal AgentSession —
the factory-call count is the pool-population count. Zero keys.
"""

from typing import Any

import pytest

from neosian import AgentConfig, Model
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.distill import DigestBatch, DigestLine
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from tests.unit.conversation.test_conversation import _SYSTEM, _ProbeStore


def _counting_config(
    *scripts: FakeScript, model: Model = Model.FAKE, **kwargs: Any
) -> tuple[AgentConfig, list[FakeClient]]:
    """One FakeClient per factory call — `len(clients)` counts pools."""
    clients: list[FakeClient] = []

    def factory(_: Any) -> FakeClient:
        clients.append(FakeClient(scripts[len(clients)]))
        return clients[-1]

    config = AgentConfig(
        system_prompt=_SYSTEM,
        model=model,
        enable_todo=False,
        client_factory=factory,
        **kwargs,
    )
    return config, clients


def _replies(*texts: str) -> FakeScript:
    return FakeScript(turns=tuple(FakeTurn(content=t) for t in texts))


# The test_conversation.py trigger lever: FAKE_SMALL's 8_192-token window
# with a tiny fraction makes the boundary fire deterministically.
_TRIGGER = CompactionConfig(hot_turns=1, trigger_fraction=0.01)


def _long(n: int) -> str:
    return "x" * 900 + f" TAIL{n}"


@pytest.mark.unit
class TestClientReuse:
    async def test_one_client_across_sends(self, store: FileStore) -> None:
        config, clients = _counting_config(_replies("one", "two"))
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("first")
        await convo.send("second")
        assert len(clients) == 1
        assert len(await store.read_turns("t1")) == 2

    async def test_streaming_shares_the_pool(self, store: FileStore) -> None:
        config, clients = _counting_config(_replies("one", "two"))
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("first")
        async for _ in await convo.send("second", stream=True):
            pass
        assert len(clients) == 1
        assert len(await store.read_turns("t1")) == 2

    async def test_client_survives_a_compaction_boundary(
        self, store: FileStore
    ) -> None:
        """`_rebind` + the acquire lease together: the boundary rebuilds
        the derived agent without reconnecting or killing the pool."""
        config, clients = _counting_config(
            _replies("r1", "r2", "r3", "r4"), model=Model.FAKE_SMALL
        )
        convo = Conversation(
            config, store=store, conversation_id="t1", compaction=_TRIGGER
        )
        await convo.send(_long(1))
        await convo.send(_long(2))
        await convo.send(_long(3))  # boundary fires here
        assert await store.read_projections("t1") != ()
        assert len(clients) == 1
        assert clients[0].closed is False
        response = await convo.send(_long(4))  # the rebound session works
        assert response.message.content == "r4"
        assert len(clients) == 1

    async def test_distillation_rides_the_same_client(self, store: FileStore) -> None:
        digest = DigestBatch(lines=[DigestLine(turn=1, line="the gist")])
        script = FakeScript(
            turns=(
                FakeTurn(content="r" * 300),
                FakeTurn(content="r2"),
                # send 3's boundary distills turn 1, then the agent runs:
                FakeTurn(content=digest.model_dump_json()),
                FakeTurn(content="r3"),
            )
        )
        config, clients = _counting_config(script, model=Model.FAKE_SMALL)
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
        await convo.send(_long(3))
        assert len(clients) == 1
        assert len(clients[0].calls) == 4  # 3 agent runs + 1 distill call

    async def test_aclose_closes_and_is_idempotent(self, store: FileStore) -> None:
        config, clients = _counting_config(_replies("one"), _replies("two"))
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("first")
        await convo.aclose()
        assert clients[0].closed is True
        await convo.aclose()  # idempotent
        # A release, not a destroy: the next send opens a fresh pool.
        response = await convo.send("second")
        assert response.message.content == "two"
        assert len(clients) == 2
        assert clients[1].closed is False

    async def test_async_with_closes_and_enter_does_no_io(self) -> None:
        probe = _ProbeStore()
        config, clients = _counting_config(_replies("one"))
        async with Conversation(config, store=probe, conversation_id="t1") as convo:
            assert probe.read_calls == 0  # lazy start survives __aenter__
            await convo.start()
            assert probe.read_calls == 1
            await convo.send("first")
        assert clients[0].closed is True

    async def test_not_closing_leaves_the_pool_open(self, store: FileStore) -> None:
        config, clients = _counting_config(_replies("one", "two"))
        convo = Conversation(config, store=store, conversation_id="t1")
        await convo.send("first")
        await convo.send("second")
        assert clients[0].closed is False  # the documented safety
