"""The Conversation's own calls on the metering seam (N7, ledger #332):
compaction and reflection fire `on_llm_call`, labelled, with the
conversation id and no run; the agent's runs carry the conversation id."""

import pytest

from neosian import AgentConfig, Model
from neosian._foundation.agent.hooks import AgentHooks, LlmCallEvent
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.distill import DigestBatch, DigestLine
from neosian._foundation.conversation.reflection import CreateOp, ReflectionBatch
from neosian._foundation.llm.base import Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore

_USAGE = Usage(input_tokens=100, output_tokens=10)


def _config(seen: list[LlmCallEvent], *turns: FakeTurn) -> AgentConfig:
    fake = FakeClient(FakeScript(turns=turns))
    return AgentConfig(
        system_prompt="You are a test agent.",
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        hooks=AgentHooks(on_llm_call=seen.append),
    )


@pytest.mark.unit
class TestTheWholeBill:
    async def test_compaction_is_on_the_bill(self, store: FileStore) -> None:
        seen: list[LlmCallEvent] = []
        digest = DigestBatch(lines=[DigestLine(turn=1, line="the gist")])
        convo = Conversation(
            _config(
                seen,
                FakeTurn(content="r" * 300),
                FakeTurn(content="r2"),
                FakeTurn(content=digest.model_dump_json(), usage=_USAGE),
            ),
            store=store,
            conversation_id="t1",
            compaction=CompactionConfig(hot_turns=1, digest_chars=100),
        )
        await convo.send("one")
        await convo.send("two")
        result = await convo.compact()
        assert result.usage == _USAGE

        assert [e.purpose for e in seen] == ["agent", "agent", "compaction"]
        assert all(e.conversation_id == "t1" for e in seen)
        agent_runs = [e.run_id for e in seen[:2]]
        assert None not in agent_runs and len(set(agent_runs)) == 2
        compaction = seen[2]
        assert compaction.run_id is None and compaction.parent_run_id is None
        assert compaction.usage == _USAGE
        assert compaction.model == Model.FAKE.value
        assert compaction.started_at is not None and compaction.error_code is None

    async def test_reflection_is_on_the_bill(self, store: FileStore) -> None:
        seen: list[LlmCallEvent] = []
        batch = ReflectionBatch(
            ops=[
                CreateOp(
                    command="create",
                    path="/memories/preferences",
                    content="Prefers espresso.",
                )
            ]
        )
        convo = Conversation(
            _config(
                seen,
                FakeTurn(content="Noted."),
                FakeTurn(content=batch.model_dump_json(), usage=_USAGE),
            ),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        await convo.send("I only drink espresso.")
        result = await convo.reflect()
        assert result.usage == _USAGE

        assert [e.purpose for e in seen] == ["agent", "reflection"]
        reflection = seen[1]
        assert reflection.conversation_id == "t1" and reflection.run_id is None
        assert reflection.usage == _USAGE

    async def test_a_degraded_call_is_still_billed(self, store: FileStore) -> None:
        seen: list[LlmCallEvent] = []
        convo = Conversation(
            _config(
                seen,
                FakeTurn(content="Noted."),
                FakeTurn(content="not the schema", usage=_USAGE),
            ),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        await convo.send("hello")
        result = await convo.reflect()
        assert result.degraded is not None and result.usage is None

        reflection = seen[1]
        assert reflection.purpose == "reflection"
        assert reflection.usage == _USAGE  # the reply arrived and was billed
