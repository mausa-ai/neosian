"""The twelfth scenario, `handoff` (§33, N6): a session another agent
continues in one call, on every transport.

The fake client replays a script without reading the request's tool
list or its prefix, so the shipped pack alone cannot tell the note was
shown, nor that the log's middle came back only through `call=`: these
pins read the captures and the prefixes."""

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
    return next(s for s in config.scenarios if s.name == "handoff")


def _base(**kwargs: object) -> AgentConfig:
    return AgentConfig(
        system_prompt="agent under test",
        model=Model.FAKE,
        enable_todo=False,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.unit
class TestHandoffScenario:
    @pytest.mark.parametrize("transport", list(Transport)[2:] + [Transport.FUNCTION])
    async def test_the_handoff_runs_on_every_transport(
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
        assert [str(c.name) for c in captures] == [
            "handoff",
            "continue_session",
            "recall_turn",
        ]
        assert all(c.executed and c.ok for c in captures)

    async def test_the_note_greets_the_next_session_and_not_the_one_after(
        self, tmp_path: Path
    ) -> None:
        """The continuing session's prefix shows the note in full and the
        fresh session's shows none; the continue text omits the log's
        middle, which the recall by call brings back whole."""
        config = _pack()
        scenario = _scenario(config)
        clients: list[FakeClient] = []
        scripts = {s.name: s.script or () for s in scenario.sessions}

        def factory(_model: AnyModel) -> FakeClient:
            fake = FakeClient(FakeScript(turns=scripts[names[len(clients)]]))
            clients.append(fake)
            return fake

        names = [s.name for s in scenario.sessions if s.record is None]
        scenario = MemoryScenario(
            name=scenario.name,
            sessions=tuple(
                s if s.record is not None else dataclasses.replace(s, script=None)
                for s in scenario.sessions
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
        departing, continuing, fresh = clients
        assert "[handoff note" not in str(departing.calls[0].messages[0].content)
        prefix = str(continuing.calls[0].messages[0].content)
        assert "[handoff note — written " in prefix
        # Unlinked (a bare agent is no session): the bare call is named,
        # and it means the session landed after the note, the build.
        assert "call continue_session() to receive" in prefix
        assert "Next: read the build id" in prefix
        delivered = str(continuing.calls[1].messages[-1].content)
        assert "[continuing conversation cc-build" in delivered
        assert "[its handoff note, written" in delivered
        assert "[1] USER: Run the build" in delivered
        recalled = str(continuing.calls[2].messages[-1].content)
        assert "BUILD-7731" in recalled and "BUILD-7731" not in delivered
        assert "[handoff note" not in str(fresh.calls[0].messages[0].content)
