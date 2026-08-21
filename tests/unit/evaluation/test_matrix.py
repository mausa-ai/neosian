"""run_evaluation — axis order, progress stream, throttle, error downgrade."""

import textwrap
from pathlib import Path

import pytest

from neosian._foundation.evaluation.matrix import _needs_throttle, run_evaluation
from neosian._foundation.evaluation.memory_types import (
    MemoryEvalConfig,
    MemoryScenario,
    MemorySession,
)
from neosian._foundation.evaluation.results import CaseStatus, ProgressEvent
from neosian._foundation.evaluation.types import (
    AgentEvalConfig,
    EvalCase,
    EvalTurn,
    Expectation,
    Variant,
)
from neosian._foundation.llm.fake import FakeTurn
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.shared.types import Model, SystemPrompt, ToolName

AGENT_FILE = """
from neosian import AgentConfig, Tool, ToolResult


@Tool(name="lookup", description="Look something up")
async def lookup(q: str) -> ToolResult[str]:
    return ToolResult.ok("real")


configuration = AgentConfig(
    system_prompt="You are a test agent.",
    tools=[lookup],
    enable_todo=False,
)
"""


def _agent(tmp_path: Path) -> str:
    path = tmp_path / "agent.py"
    path.write_text(textwrap.dedent(AGENT_FILE))
    return str(path)


def _case(name: str, content: str = "quiet") -> EvalCase:
    return EvalCase(
        name=name,
        turns=(EvalTurn(user="hi", expect=Expectation(no_tool=True)),),
        script=(FakeTurn(content=content),),
    )


@pytest.mark.unit
class TestMatrix:
    async def test_axis_order_and_report_shape(self, tmp_path: Path) -> None:
        config = AgentEvalConfig(
            name="suite",
            agent=_agent(tmp_path),
            models=(Model.FAKE, Model.FAKE_SMALL),
            cases=(_case("one"), _case("two")),
            variants=(
                Variant(name="a", system_prompt=SystemPrompt("A.")),
                Variant(name="b", system_prompt=SystemPrompt("B.")),
            ),
        )
        report = await run_evaluation(config)

        assert report.suite == "suite"
        assert report.variants == ("a", "b")
        assert report.models == ("fake", "fake-small")
        assert report.cases == ("one", "two")
        assert report.total == 8
        assert report.passed == 8
        assert [(r.variant, r.model, r.case) for r in report.results] == [
            ("a", "fake", "one"),
            ("a", "fake", "two"),
            ("a", "fake-small", "one"),
            ("a", "fake-small", "two"),
            ("b", "fake", "one"),
            ("b", "fake", "two"),
            ("b", "fake-small", "one"),
            ("b", "fake-small", "two"),
        ]
        cell = report.result_for("b", "fake-small", "two")
        assert cell is not None and cell.passed is True
        assert report.result_for("z", "fake", "one") is None

    async def test_progress_stream(self, tmp_path: Path) -> None:
        config = AgentEvalConfig(
            name="suite",
            agent=_agent(tmp_path),
            models=(Model.FAKE,),
            cases=(_case("one"), _case("two")),
        )
        events: list[ProgressEvent] = []
        await run_evaluation(config, on_progress=events.append)

        assert [(e.case_index, e.status) for e in events] == [
            (0, CaseStatus.RUNNING),
            (0, CaseStatus.PASSED),
            (1, CaseStatus.RUNNING),
            (1, CaseStatus.PASSED),
        ]
        assert all(e.variant_index == 0 and e.model_index == 0 for e in events)

    async def test_a_bad_case_downgrades_without_killing_the_suite(
        self, tmp_path: Path
    ) -> None:
        bad = EvalCase(
            name="bad",
            turns=(EvalTurn(user="hi", expect=Expectation(no_tool=True)),),
            script=(FakeTurn(content="x"),),
            execute_tools=frozenset({ToolName("nope")}),
        )
        config = AgentEvalConfig(
            name="suite",
            agent=_agent(tmp_path),
            models=(Model.FAKE,),
            cases=(bad, _case("good")),
        )
        report = await run_evaluation(config)

        assert report.total == 2
        assert report.failed == 1
        failed = report.result_for("base", "fake", "bad")
        assert failed is not None
        assert failed.passed is False
        assert failed.error is not None and "unknown tool" in failed.error
        good = report.result_for("base", "fake", "good")
        assert good is not None and good.passed is True


@pytest.mark.unit
class TestKindDispatch:
    async def test_memory_config_routes_to_the_memory_matrix(
        self, tmp_path: Path
    ) -> None:
        """The one dispatch branch (§13.12): a memory suite runs its
        scenarios and reports transports on the variants axis."""
        config = MemoryEvalConfig(
            name="mem",
            agent=_agent(tmp_path),
            models=(Model.FAKE,),
            mounts=(Mount(scope="user:eval", mount_path="user"),),
            scenarios=(
                MemoryScenario(
                    name="s",
                    sessions=(
                        MemorySession(
                            name="one",
                            turns=(
                                EvalTurn(user="hi", expect=Expectation(no_tool=True)),
                            ),
                            script=(FakeTurn(content="quiet"),),
                        ),
                    ),
                ),
            ),
        )
        events: list[ProgressEvent] = []
        report = await run_evaluation(
            config, on_progress=events.append, store_root=tmp_path / "stores"
        )

        assert report.suite == "mem"
        assert report.variants == ("function",)
        assert report.models == ("fake",)
        assert report.cases == ("s",)
        assert report.total == 1 and report.passed == 1
        assert [e.status for e in events] == [
            CaseStatus.RUNNING,
            CaseStatus.PASSED,
        ]
        assert (tmp_path / "stores" / "function" / "fake" / "s").is_dir()


@pytest.mark.unit
class TestThrottleExemption:
    def test_scripted_and_fake_runs_skip_the_throttle(self) -> None:
        scripted = _case("s")
        plain = EvalCase(
            name="p", turns=(EvalTurn(user="x", expect=Expectation(no_tool=True)),)
        )
        assert _needs_throttle(Model.FAKE, plain) is False
        assert _needs_throttle(Model.CEREBRAS_GPT_OSS_120B, scripted) is False
        assert _needs_throttle(Model.CEREBRAS_GPT_OSS_120B, plain) is True
