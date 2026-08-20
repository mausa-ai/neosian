"""Progress display state over the variants × models × cases axes."""

import pytest
from rich.tree import Tree

from neosian._foundation.evaluation.progress import (
    EvalProgress,
    _format_latency,
    create_progress_callback,
)
from neosian._foundation.evaluation.results import CaseStatus, ProgressEvent
from neosian._foundation.evaluation.types import (
    AgentEvalConfig,
    EvalCase,
    EvalTurn,
    Expectation,
    Variant,
)
from neosian._foundation.shared.types import Model, SystemPrompt


def _config() -> AgentEvalConfig:
    case = EvalCase(
        name="c1", turns=(EvalTurn(user="hi", expect=Expectation(no_tool=True)),)
    )
    other = EvalCase(
        name="c2", turns=(EvalTurn(user="yo", expect=Expectation(no_tool=True)),)
    )
    return AgentEvalConfig(
        name="suite",
        agent="agent.py",
        models=(Model.FAKE, Model.FAKE_SMALL),
        cases=(case, other),
        variants=(
            Variant(name="a", system_prompt=SystemPrompt("A.")),
            Variant(name="b", system_prompt=SystemPrompt("B.")),
        ),
    )


@pytest.mark.unit
class TestEvalProgress:
    def test_state_mirrors_the_axes(self) -> None:
        progress = EvalProgress(_config())
        assert [v.variant for v in progress.variants] == ["a", "b"]
        assert [m.model for m in progress.variants[0].models] == [
            "fake",
            "fake-small",
        ]
        assert [c.name for c in progress.variants[0].models[0].cases] == ["c1", "c2"]
        assert all(
            c.status is CaseStatus.PENDING
            for v in progress.variants
            for m in v.models
            for c in m.cases
        )

    def test_update_targets_one_cell(self) -> None:
        progress = EvalProgress(_config())

        def cell() -> object:
            return progress.variants[1].models[0].cases[1].status

        progress.update(ProgressEvent(1, 0, 1, CaseStatus.RUNNING))
        assert cell() is CaseStatus.RUNNING
        progress.update(ProgressEvent(1, 0, 1, CaseStatus.PASSED, 250.0))
        assert cell() is CaseStatus.PASSED
        assert progress.variants[1].models[0].cases[1].latency_ms == 250.0
        assert progress.variants[0].models[0].cases[0].status is CaseStatus.PENDING

    def test_completion_rolls_up_per_model(self) -> None:
        progress = EvalProgress(_config())

        def model_state() -> tuple[bool, float]:
            state = progress.variants[0].models[0]
            return state.is_complete, state.total_latency_ms

        assert model_state() == (False, 0.0)
        progress.update(ProgressEvent(0, 0, 0, CaseStatus.PASSED, 100.0))
        progress.update(ProgressEvent(0, 0, 1, CaseStatus.FAILED, 50.0))
        assert model_state() == (True, 150.0)

    def test_build_tree_returns_a_tree(self) -> None:
        progress = EvalProgress(_config())
        progress.update(ProgressEvent(0, 0, 0, CaseStatus.PASSED, 1500.0))
        assert isinstance(progress._build_tree(), Tree)

    def test_callback_is_the_update_method(self) -> None:
        progress = EvalProgress(_config())
        callback = create_progress_callback(progress)
        callback(ProgressEvent(0, 1, 0, CaseStatus.RUNNING))
        assert progress.variants[0].models[1].cases[0].status is CaseStatus.RUNNING


@pytest.mark.unit
class TestLatencyFormat:
    def test_sub_second_and_seconds(self) -> None:
        assert _format_latency(250.0) == "250ms"
        assert _format_latency(1500.0) == "1.5s"
