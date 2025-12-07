"""Unit tests for prompt loading."""

import tempfile
from pathlib import Path

import pytest

from neosian._foundation.shared.exceptions import (
    PromptFileNotFoundError,
    PromptInvalidYAMLError,
    PromptMissingKeyError,
)
from neosian._foundation.shared.prompt import load_prompt


@pytest.mark.unit
class TestLoadPrompt:
    """Tests for load_prompt function."""

    def test_loads_valid_yaml(self) -> None:
        """Test loading a valid YAML prompt file."""
        yaml_content = """
system_prompt: |
  You are a helpful assistant.
  Be concise.
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            prompt = load_prompt(f.name)

            assert "You are a helpful assistant." in prompt
            assert "Be concise." in prompt

    def test_loads_simple_string(self) -> None:
        """Test loading a simple string prompt."""
        yaml_content = """
system_prompt: "You are helpful."
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            prompt = load_prompt(f.name)

            assert prompt == "You are helpful."

    def test_raises_on_file_not_found(self) -> None:
        """Test error when file doesn't exist."""
        with pytest.raises(PromptFileNotFoundError):
            load_prompt("/nonexistent/path/prompt.yaml")

    def test_raises_on_invalid_yaml(self) -> None:
        """Test error when YAML is invalid."""
        yaml_content = """
system_prompt: "unclosed string
  more text
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(PromptInvalidYAMLError):
                load_prompt(f.name)

    def test_raises_on_missing_system_prompt_key(self) -> None:
        """Test error when system_prompt key is missing."""
        yaml_content = """
other_key: "some value"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(PromptMissingKeyError):
                load_prompt(f.name)

    def test_raises_on_non_string_prompt(self) -> None:
        """Test error when system_prompt is not a string."""
        yaml_content = """
system_prompt:
  - item1
  - item2
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(PromptInvalidYAMLError):
                load_prompt(f.name)

    def test_raises_on_non_dict_yaml(self) -> None:
        """Test error when YAML root is not a dict."""
        yaml_content = """
- item1
- item2
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(PromptInvalidYAMLError):
                load_prompt(f.name)

    def test_accepts_path_object(self) -> None:
        """Test that Path objects are accepted."""
        yaml_content = """
system_prompt: "Hello"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            prompt = load_prompt(Path(f.name))

            assert prompt == "Hello"
