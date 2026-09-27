"""The eleventh scenario, `old-turn-search` (§32, N5): a fact that lives
only in an old, unfiled turn is found by content on every transport.

The fake client replays a script without reading the request's tool
list, so the shipped pack alone cannot tell a served `search_history`
from a missing one: these pins read the captures."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.evaluation.memory_runner import run_scenario
from neosian._foundation.evaluation.memory_types import (
    MemoryEvalConfig,
    MemoryScenario,
    Transport,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript
from neosian._foundation.shared.types import AgentConfig, AnyModel, Model

_PACK = Path(__file__).resolve().parents[3] / "examples" / "eval_memory_baseline.yaml"


def _pack() -> MemoryEvalConfig:
    config = load_eval_config(_PACK)
    assert isinstance(config, MemoryEvalConfig)
    return config


def _scenario(config: MemoryEvalConfig) -> MemoryScenario:
    return next(s for s in config.scenarios if s.name == "old-turn-search")


def _base(**kwargs: object) -> AgentConfig:
    return AgentConfig(
        system_prompt="agent under test",
        model=Model.FAKE,
        enable_todo=False,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.unit
class TestOldTurnSearch:
    @pytest.mark.parametrize("transport", list(Transport)[2:] + [Transport.FUNCTION])
    async def test_the_search_and_the_recall_run_on_every_transport(
        self, tmp_path: Path, transport: Transport
    ) -> None:
        config = _pack()
        result = await run_scenario(
            _base(),
            transport,
            Model.FAKE,
            _scenario(config),
            mounts=config.mounts,
            store_root=tmp_path / "store",
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        captures = [c for t in result.turns for c in t.tool_calls]
        assert [str(c.name) for c in captures] == ["search_history", "recall_turn"]
        assert all(c.executed and c.ok for c in captures)

    async def test_the_prefix_cannot_answer(self, tmp_path: Path) -> None:
        """The fact is past the digest and its session outside the three
        shown, so only the search reaches it."""
        config = _pack()
        scenario = _scenario(config)
        reader = scenario.sessions[-1]
        clients: list[FakeClient] = []

        def factory(_model: AnyModel) -> FakeClient:
            fake = FakeClient(FakeScript(turns=reader.script or ()))
            clients.append(fake)
            return fake

        scenario = MemoryScenario(
            name=scenario.name,
            sessions=(
                *scenario.sessions[:-1],
                dataclasses.replace(reader, script=None),
            ),
        )
        result = await run_scenario(
            _base(client_factory=factory),
            Transport.FUNCTION,
            Model.FAKE,
            scenario,
            mounts=config.mounts,
            store_root=tmp_path / "store",
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        (fake,) = clients
        system = str(fake.calls[0].messages[0].content)
        assert "- /project/sessions/cc-old-1" in system  # listed in the index
        assert "[conversation cc-old-1" not in system  # outside the recent three
        assert "86400" not in system and "CACHE7731" not in system
        search_result = str(fake.calls[1].messages[-1].content)
        assert "[cc-old-1 #1]" in search_result and "86400" not in search_result
        recall_result = str(fake.calls[2].messages[-1].content)
        assert "86400" in recall_result and "CACHE7731" in recall_result
