"""Unit tests for evaluation config loader."""

from pathlib import Path

import pytest

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigNotFoundError,
)


class TestLoadEvalConfig:
    """Tests for load_eval_config function."""

    def test_load_valid_one_shot_config(self, tmp_path: Path) -> None:
        """Test loading a valid one-shot evaluation config."""
        config_content = """
name: Test Evaluation
prompts:
  - prompts/agent_v1.yaml
  - prompts/agent_v2.yaml
models:
  - groq:llama-3.3-70b-versatile
  - openai:gpt-4o
cases:
  - name: simple_generation
    input: "Generate an image of a cat"
    expect:
      tool: generate_image
      params:
        prompt: "_exists"
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        config = load_eval_config(config_file)

        assert config.name == "Test Evaluation"
        assert len(config.prompts) == 2
        assert len(config.models) == 2
        assert len(config.cases) == 1
        assert config.cases[0].name == "simple_generation"
        assert config.cases[0].input == "Generate an image of a cat"
        assert config.cases[0].expect is not None
        assert config.cases[0].expect.tool == "generate_image"

    def test_load_valid_conversational_config(self, tmp_path: Path) -> None:
        """Test loading a config with conversational cases."""
        config_content = """
name: Conversational Test
prompts:
  - prompts/agent.yaml
models:
  - groq:llama-3.3-70b-versatile
cases:
  - name: multi_turn
    conversation:
      - user: "Generate a portrait of a woman"
        expect:
          tool: generate_image
          params:
            prompt: "_exists"
      - user: "Now make her blonde"
        expect:
          tool: edit_image
          params:
            change: blonde hair
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        config = load_eval_config(config_file)

        assert config.name == "Conversational Test"
        assert len(config.cases) == 1
        case = config.cases[0]
        assert case.is_conversational is True
        assert case.conversation is not None
        assert len(case.conversation) == 2
        assert case.conversation[0].user == "Generate a portrait of a woman"
        assert case.conversation[1].expect.tool == "edit_image"

    def test_load_config_not_found(self) -> None:
        """Test error when config file doesn't exist."""
        with pytest.raises(EvalConfigNotFoundError):
            load_eval_config("/nonexistent/path/eval.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        """Test error with invalid YAML syntax."""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text("name: [invalid yaml\n  broken:")

        with pytest.raises(EvalConfigInvalidYAMLError):
            load_eval_config(config_file)

    def test_load_missing_name_key(self, tmp_path: Path) -> None:
        """Test error when name key is missing."""
        config_content = """
prompts:
  - prompts/agent.yaml
models:
  - groq:model
cases:
  - name: test
    input: "Hello"
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        with pytest.raises(EvalConfigMissingKeyError) as exc_info:
            load_eval_config(config_file)
        assert "name" in str(exc_info.value)

    def test_load_missing_prompts_key(self, tmp_path: Path) -> None:
        """Test error when prompts key is missing."""
        config_content = """
name: Test
models:
  - groq:model
cases:
  - name: test
    input: "Hello"
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        with pytest.raises(EvalConfigMissingKeyError) as exc_info:
            load_eval_config(config_file)
        assert "prompts" in str(exc_info.value)

    def test_load_missing_models_key(self, tmp_path: Path) -> None:
        """Test error when models key is missing."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
cases:
  - name: test
    input: "Hello"
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        with pytest.raises(EvalConfigMissingKeyError) as exc_info:
            load_eval_config(config_file)
        assert "models" in str(exc_info.value)

    def test_load_missing_cases_key(self, tmp_path: Path) -> None:
        """Test error when cases key is missing."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
models:
  - groq:model
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        with pytest.raises(EvalConfigMissingKeyError) as exc_info:
            load_eval_config(config_file)
        assert "cases" in str(exc_info.value)

    def test_load_case_missing_name(self, tmp_path: Path) -> None:
        """Test error when case is missing name field."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
models:
  - groq:model
cases:
  - input: "Hello"
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        with pytest.raises(EvalCaseInvalidError) as exc_info:
            load_eval_config(config_file)
        assert "name" in str(exc_info.value)

    def test_load_case_missing_input_and_conversation(self, tmp_path: Path) -> None:
        """Test error when case has neither input nor conversation."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
models:
  - groq:model
cases:
  - name: bad_case
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        with pytest.raises(EvalCaseInvalidError) as exc_info:
            load_eval_config(config_file)
        assert "input" in str(exc_info.value) or "conversation" in str(exc_info.value)

    def test_load_conversation_missing_user(self, tmp_path: Path) -> None:
        """Test error when conversation turn is missing user field."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
models:
  - groq:model
cases:
  - name: test
    conversation:
      - expect:
          tool: something
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        with pytest.raises(EvalCaseInvalidError) as exc_info:
            load_eval_config(config_file)
        assert "user" in str(exc_info.value)

    def test_load_no_tool_expectation(self, tmp_path: Path) -> None:
        """Test loading case with no_tool expectation."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
models:
  - groq:model
cases:
  - name: greeting
    input: "Hello, how are you?"
    expect:
      no_tool: true
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        config = load_eval_config(config_file)

        assert config.cases[0].expect is not None
        assert config.cases[0].expect.no_tool is True

    def test_load_sequence_expectation(self, tmp_path: Path) -> None:
        """Test loading case with sequence expectation."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
models:
  - groq:model
cases:
  - name: multi_tool
    input: "Search and summarize"
    expect:
      sequence:
        - tool: search
          params:
            query: "_exists"
        - tool: summarize
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        config = load_eval_config(config_file)

        assert config.cases[0].expect is not None
        assert config.cases[0].expect.sequence is not None
        assert len(config.cases[0].expect.sequence) == 2
        assert config.cases[0].expect.sequence[0]["tool"] == "search"

    def test_load_case_without_expect(self, tmp_path: Path) -> None:
        """Test loading case without expect block."""
        config_content = """
name: Test
prompts:
  - prompts/agent.yaml
models:
  - groq:model
cases:
  - name: freeform
    input: "Tell me a joke"
"""
        config_file = tmp_path / "eval.yaml"
        config_file.write_text(config_content)

        config = load_eval_config(config_file)

        assert config.cases[0].expect is not None
        assert config.cases[0].expect.tool is None
        assert config.cases[0].expect.no_tool is False
