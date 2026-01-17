"""Unit tests for evaluation reporter."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from neosian._foundation.evaluation.reporter import (
    print_results,
    save_results,
)
from neosian._foundation.shared.types import (
    EvalCase,
    EvalConfig,
    EvalResult,
    TurnResult,
)


class TestSaveResults:
    """Tests for save_results function."""

    def test_save_results_creates_file(self, tmp_path: Path) -> None:
        """Test that save_results creates a JSON file."""
        config = EvalConfig(
            name="Test Eval",
            prompts=["prompts/v1.yaml"],
            models=["groq:llama-3.3-70b"],
            cases=[EvalCase(name="test", input="hello")],
        )
        results = [
            EvalResult(
                case_name="test",
                prompt_file="prompts/v1.yaml",
                model="groq:llama-3.3-70b",
                passed=True,
            )
        ]

        output_path = save_results(config, results, output_dir=str(tmp_path))

        assert output_path.exists()
        assert output_path.suffix == ".json"

    def test_save_results_json_structure(self, tmp_path: Path) -> None:
        """Test the structure of saved JSON file."""
        config = EvalConfig(
            name="Test Eval",
            prompts=["prompts/v1.yaml", "prompts/v2.yaml"],
            models=["groq:model1", "openai:model2"],
            cases=[
                EvalCase(name="case1", input="input1"),
                EvalCase(name="case2", input="input2"),
            ],
        )
        results = [
            EvalResult(
                case_name="case1",
                prompt_file="prompts/v1.yaml",
                model="groq:model1",
                passed=True,
                tool_sequence=["tool_a"],
            ),
            EvalResult(
                case_name="case1",
                prompt_file="prompts/v1.yaml",
                model="groq:model1",
                passed=False,
                error="Something failed",
            ),
        ]

        output_path = save_results(config, results, output_dir=str(tmp_path))

        with open(output_path) as f:
            data = json.load(f)

        assert "timestamp" in data
        assert data["config"]["name"] == "Test Eval"
        assert data["config"]["prompts"] == ["prompts/v1.yaml", "prompts/v2.yaml"]
        assert data["config"]["models"] == ["groq:model1", "openai:model2"]
        assert data["summary"]["total"] == 2
        assert data["summary"]["passed"] == 1
        assert data["summary"]["failed"] == 1
        assert len(data["results"]) == 2

    def test_save_results_includes_turn_details(self, tmp_path: Path) -> None:
        """Test that turn details are included in saved results."""
        config = EvalConfig(
            name="Test",
            prompts=["p.yaml"],
            models=["groq:m"],
            cases=[EvalCase(name="c", input="i")],
        )
        results = [
            EvalResult(
                case_name="c",
                prompt_file="p.yaml",
                model="groq:m",
                passed=False,
                turns=[
                    TurnResult(
                        turn_index=0,
                        passed=False,
                        expected_tool="expected",
                        actual_tool="actual",
                        expected_params={"a": 1},
                        actual_params={"a": 2},
                        param_failures=["a: mismatch"],
                        actual_response="response text",
                        error="param mismatch",
                    )
                ],
            )
        ]

        output_path = save_results(config, results, output_dir=str(tmp_path))

        with open(output_path) as f:
            data = json.load(f)

        turn = data["results"][0]["turns"][0]
        assert turn["turn_index"] == 0
        assert turn["passed"] is False
        assert turn["expected_tool"] == "expected"
        assert turn["actual_tool"] == "actual"
        assert turn["expected_params"] == {"a": 1}
        assert turn["actual_params"] == {"a": 2}
        assert turn["param_failures"] == ["a: mismatch"]
        assert turn["actual_response"] == "response text"
        assert turn["error"] == "param mismatch"

    def test_save_results_creates_directory(self, tmp_path: Path) -> None:
        """Test that save_results creates output directory if needed."""
        nested_dir = tmp_path / "deeply" / "nested" / "evals"
        config = EvalConfig(
            name="Test",
            prompts=["p.yaml"],
            models=["m"],
            cases=[EvalCase(name="c", input="i")],
        )
        results = [
            EvalResult(case_name="c", prompt_file="p.yaml", model="m", passed=True)
        ]

        output_path = save_results(config, results, output_dir=str(nested_dir))

        assert nested_dir.exists()
        assert output_path.exists()


class TestPrintResults:
    """Tests for print_results function."""

    def test_print_results_no_errors(self) -> None:
        """Test that print_results runs without errors."""
        config = EvalConfig(
            name="Test Eval",
            prompts=["prompts/v1.yaml"],
            models=["groq:llama-3.3-70b"],
            cases=[EvalCase(name="test", input="hello")],
        )
        results = [
            EvalResult(
                case_name="test",
                prompt_file="prompts/v1.yaml",
                model="groq:llama-3.3-70b",
                passed=True,
            )
        ]

        mock_console = MagicMock()
        print_results(config, results, mock_console)

        # Verify console.print was called
        assert mock_console.print.called

    def test_print_results_with_failures(self) -> None:
        """Test print_results displays failures."""
        config = EvalConfig(
            name="Test Eval",
            prompts=["prompts/v1.yaml"],
            models=["groq:model"],
            cases=[EvalCase(name="test", input="hello")],
        )
        results = [
            EvalResult(
                case_name="test",
                prompt_file="prompts/v1.yaml",
                model="groq:model",
                passed=False,
                error="Tool mismatch",
                turns=[
                    TurnResult(
                        turn_index=0,
                        passed=False,
                        error="Expected 'a', got 'b'",
                    )
                ],
            )
        ]

        mock_console = MagicMock()
        print_results(config, results, mock_console)

        # Verify failure section was printed
        assert mock_console.print.called

    def test_print_results_multiple_models(self) -> None:
        """Test print_results with multiple models creates tables."""
        config = EvalConfig(
            name="Multi Model Test",
            prompts=["p.yaml"],
            models=["groq:model1", "openai:model2"],
            cases=[EvalCase(name="c", input="i")],
        )
        results = [
            EvalResult(
                case_name="c", prompt_file="p.yaml", model="groq:model1", passed=True
            ),
            EvalResult(
                case_name="c", prompt_file="p.yaml", model="openai:model2", passed=False
            ),
        ]

        mock_console = MagicMock()
        print_results(config, results, mock_console)

        assert mock_console.print.called

    def test_print_results_conversational_shows_turn_count(self) -> None:
        """Test print_results shows turn counts for conversational cases."""
        config = EvalConfig(
            name="Conv Test",
            prompts=["p.yaml"],
            models=["groq:model"],
            cases=[EvalCase(name="conv", input=None, conversation=[])],
        )
        results = [
            EvalResult(
                case_name="conv",
                prompt_file="p.yaml",
                model="groq:model",
                passed=True,
                turns=[
                    TurnResult(turn_index=0, passed=True),
                    TurnResult(turn_index=1, passed=True),
                    TurnResult(turn_index=2, passed=True),
                ],
            )
        ]

        mock_console = MagicMock()
        print_results(config, results, mock_console)

        # total_turns and pass_count should be calculated from turns
        assert results[0].total_turns == 3
        assert results[0].pass_count == 3
