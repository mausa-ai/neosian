"""Unit tests for evaluation progress display."""

import pytest

from neosian._foundation.evaluation.progress import (
    CaseStatus,
    EvalProgress,
    ModelStatus,
    PromptStatus,
    create_progress_callback,
)
from neosian._foundation.shared.types import EvalCase, EvalConfig, Expectation


@pytest.fixture
def simple_config() -> EvalConfig:
    """Create a simple eval config for testing."""
    return EvalConfig(
        name="Test Eval",
        prompts=["prompts/minimal.yaml", "prompts/verbose.yaml"],
        models=["openai:gpt-5", "anthropic:haiku"],
        cases=[
            EvalCase(
                name="test_image",
                input="Create an image",
                expect=Expectation(tool="generate_image"),
            ),
            EvalCase(
                name="test_tts",
                input="Say hello",
                expect=Expectation(tool="generate_tts"),
            ),
        ],
    )


class TestCaseStatus:
    """Tests for CaseStatus dataclass."""

    def test_default_status_is_pending(self) -> None:
        """Default status should be pending."""
        status = CaseStatus(name="test")
        assert status.status == "pending"

    def test_default_latency_is_zero(self) -> None:
        """Default latency should be zero."""
        status = CaseStatus(name="test")
        assert status.latency_ms == 0.0

    def test_custom_status(self) -> None:
        """Can set custom status."""
        status = CaseStatus(name="test", status="passed")
        assert status.status == "passed"

    def test_custom_latency(self) -> None:
        """Can set custom latency."""
        status = CaseStatus(name="test", status="passed", latency_ms=150.5)
        assert status.latency_ms == 150.5


class TestModelStatus:
    """Tests for ModelStatus dataclass."""

    def test_default_cases_empty(self) -> None:
        """Default cases list should be empty."""
        status = ModelStatus(model="gpt-5")
        assert status.cases == []

    def test_with_cases(self) -> None:
        """Can add cases."""
        status = ModelStatus(
            model="gpt-5",
            cases=[CaseStatus(name="test1"), CaseStatus(name="test2")],
        )
        assert len(status.cases) == 2

    def test_total_latency_ms(self) -> None:
        """Total latency should sum all case latencies."""
        status = ModelStatus(
            model="gpt-5",
            cases=[
                CaseStatus(name="test1", status="passed", latency_ms=100.0),
                CaseStatus(name="test2", status="passed", latency_ms=200.0),
            ],
        )
        assert status.total_latency_ms == 300.0

    def test_is_complete_all_passed(self) -> None:
        """Is complete when all cases passed."""
        status = ModelStatus(
            model="gpt-5",
            cases=[
                CaseStatus(name="test1", status="passed"),
                CaseStatus(name="test2", status="passed"),
            ],
        )
        assert status.is_complete is True

    def test_is_complete_mixed(self) -> None:
        """Is complete when all cases done (passed or failed)."""
        status = ModelStatus(
            model="gpt-5",
            cases=[
                CaseStatus(name="test1", status="passed"),
                CaseStatus(name="test2", status="failed"),
            ],
        )
        assert status.is_complete is True

    def test_is_not_complete_with_pending(self) -> None:
        """Not complete when cases still pending."""
        status = ModelStatus(
            model="gpt-5",
            cases=[
                CaseStatus(name="test1", status="passed"),
                CaseStatus(name="test2", status="pending"),
            ],
        )
        assert status.is_complete is False


class TestPromptStatus:
    """Tests for PromptStatus dataclass."""

    def test_default_models_empty(self) -> None:
        """Default models list should be empty."""
        status = PromptStatus(prompt="minimal.yaml")
        assert status.models == []


class TestEvalProgress:
    """Tests for EvalProgress class."""

    def test_init_creates_tree_structure(self, simple_config: EvalConfig) -> None:
        """Should create correct tree structure from config."""
        progress = EvalProgress(simple_config)

        # 2 prompts
        assert len(progress.prompts) == 2

        # Each prompt has 2 models
        for prompt_status in progress.prompts:
            assert len(prompt_status.models) == 2

            # Each model has 2 cases
            for model_status in prompt_status.models:
                assert len(model_status.cases) == 2

    def test_all_cases_start_pending(self, simple_config: EvalConfig) -> None:
        """All cases should start with pending status."""
        progress = EvalProgress(simple_config)

        for prompt_status in progress.prompts:
            for model_status in prompt_status.models:
                for case_status in model_status.cases:
                    assert case_status.status == "pending"

    def test_update_changes_status(self, simple_config: EvalConfig) -> None:
        """Update should change case status."""
        progress = EvalProgress(simple_config)

        # Update first case of first model of first prompt
        progress.update(0, 0, 0, "running")
        assert progress.prompts[0].models[0].cases[0].status == "running"

        progress.update(0, 0, 0, "passed")
        assert progress.prompts[0].models[0].cases[0].status == "passed"

    def test_update_specific_case(self, simple_config: EvalConfig) -> None:
        """Update should only affect specific case."""
        progress = EvalProgress(simple_config)

        # Update second case of second model of first prompt
        progress.update(0, 1, 1, "failed")

        # First prompt, first model, first case still pending
        assert progress.prompts[0].models[0].cases[0].status == "pending"

        # First prompt, first model, second case still pending
        assert progress.prompts[0].models[0].cases[1].status == "pending"

        # First prompt, second model, first case still pending
        assert progress.prompts[0].models[1].cases[0].status == "pending"

        # First prompt, second model, second case is failed
        assert progress.prompts[0].models[1].cases[1].status == "failed"

    def test_short_model_name_with_provider(self, simple_config: EvalConfig) -> None:
        """Should extract short model name from provider:model format."""
        progress = EvalProgress(simple_config)
        assert progress._short_model_name("openai:gpt-5") == "gpt-5"
        assert progress._short_model_name("anthropic:claude-haiku") == "claude-haiku"

    def test_short_model_name_without_provider(self, simple_config: EvalConfig) -> None:
        """Should return model name as-is if no provider prefix."""
        progress = EvalProgress(simple_config)
        assert progress._short_model_name("gpt-5") == "gpt-5"

    def test_get_indicator_pending(self, simple_config: EvalConfig) -> None:
        """Pending indicator should be dim."""
        progress = EvalProgress(simple_config)
        indicator = progress._get_indicator("pending")
        assert indicator.plain == "○"
        assert indicator.style == "dim"

    def test_get_indicator_running(self, simple_config: EvalConfig) -> None:
        """Running indicator should be yellow bold."""
        progress = EvalProgress(simple_config)
        indicator = progress._get_indicator("running")
        assert indicator.plain == "◐"
        assert "yellow" in str(indicator.style)

    def test_get_indicator_passed(self, simple_config: EvalConfig) -> None:
        """Passed indicator should be green."""
        progress = EvalProgress(simple_config)
        indicator = progress._get_indicator("passed")
        assert indicator.plain == "●"
        assert indicator.style == "green"

    def test_get_indicator_failed(self, simple_config: EvalConfig) -> None:
        """Failed indicator should be red bold."""
        progress = EvalProgress(simple_config)
        indicator = progress._get_indicator("failed")
        assert indicator.plain == "✗"
        assert "red" in str(indicator.style)

    def test_build_tree_returns_tree(self, simple_config: EvalConfig) -> None:
        """_build_tree should return a Rich Tree."""
        from rich.tree import Tree

        progress = EvalProgress(simple_config)
        tree = progress._build_tree()
        assert isinstance(tree, Tree)

    def test_format_latency_milliseconds(self, simple_config: EvalConfig) -> None:
        """Format latency under 1s as milliseconds."""
        progress = EvalProgress(simple_config)
        assert progress._format_latency(150.0) == "150ms"
        assert progress._format_latency(999.0) == "999ms"

    def test_format_latency_seconds(self, simple_config: EvalConfig) -> None:
        """Format latency over 1s as seconds."""
        progress = EvalProgress(simple_config)
        assert progress._format_latency(1000.0) == "1.0s"
        assert progress._format_latency(1500.0) == "1.5s"
        assert progress._format_latency(12345.0) == "12.3s"

    def test_update_with_latency(self, simple_config: EvalConfig) -> None:
        """Update should set both status and latency."""
        progress = EvalProgress(simple_config)
        progress.update(0, 0, 0, "passed", 150.5)

        case = progress.prompts[0].models[0].cases[0]
        assert case.status == "passed"
        assert case.latency_ms == 150.5


class TestCreateProgressCallback:
    """Tests for create_progress_callback function."""

    def test_callback_updates_progress(self, simple_config: EvalConfig) -> None:
        """Callback should update progress status and latency."""
        progress = EvalProgress(simple_config)
        callback = create_progress_callback(progress)

        # Initial state
        assert progress.prompts[0].models[0].cases[0].status == "pending"
        assert progress.prompts[0].models[0].cases[0].latency_ms == 0.0

        # Call callback with running (no latency)
        callback(0, 0, 0, "running", 0.0)
        assert progress.prompts[0].models[0].cases[0].status == "running"

        # Call callback with passed and latency
        callback(0, 0, 0, "passed", 150.5)
        assert progress.prompts[0].models[0].cases[0].status == "passed"
        assert progress.prompts[0].models[0].cases[0].latency_ms == 150.5

    def test_callback_signature_matches_runner(self, simple_config: EvalConfig) -> None:
        """Callback should accept (prompt_idx, model_idx, case_idx, status, latency_ms)."""
        progress = EvalProgress(simple_config)
        callback = create_progress_callback(progress)

        # Should not raise
        callback(1, 1, 1, "failed", 200.0)
        assert progress.prompts[1].models[1].cases[1].status == "failed"
        assert progress.prompts[1].models[1].cases[1].latency_ms == 200.0
