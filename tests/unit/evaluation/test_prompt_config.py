"""Tests for prompt config loader."""

from pathlib import Path

import pytest

from neosian._foundation.evaluation.prompt_config import load_prompt_config
from neosian._foundation.shared.exceptions import (
    PromptFileNotFoundError,
    PromptInvalidYAMLError,
    PromptMissingKeyError,
)
from neosian._foundation.shared.types import PromptConfig, ToolPromptConfig


@pytest.mark.unit
class TestLoadPromptConfig:
    """Tests for load_prompt_config function."""

    def test_minimal_config(self, tmp_path: Path) -> None:
        """Load config with only system_prompt."""
        config_file = tmp_path / "minimal.yaml"
        config_file.write_text(
            """
system_prompt: You are a helpful assistant.
"""
        )

        config = load_prompt_config(config_file)

        assert config.system_prompt == "You are a helpful assistant."
        assert config.tools == {}
        assert config.compact_summarize is None

    def test_full_config(self, tmp_path: Path) -> None:
        """Load config with all fields."""
        config_file = tmp_path / "full.yaml"
        config_file.write_text(
            """
system_prompt: |
  You are Mausa, a creative AI assistant.

tools:
  generate_image:
    description: |
      Create or edit images.
    on_success: |
      Call present_options after success.
  generate_tts:
    description: Text to speech.

compact_summarize: |
  Summarize for continuity.
"""
        )

        config = load_prompt_config(config_file)

        assert "Mausa" in config.system_prompt
        assert len(config.tools) == 2
        assert "generate_image" in config.tools
        assert "generate_tts" in config.tools
        assert "Create or edit" in config.tools["generate_image"].description
        assert "present_options" in (config.tools["generate_image"].on_success or "")
        assert config.tools["generate_tts"].on_success is None
        assert "continuity" in (config.compact_summarize or "")

    def test_tool_without_on_success(self, tmp_path: Path) -> None:
        """Tool without on_success field is valid."""
        config_file = tmp_path / "no_on_success.yaml"
        config_file.write_text(
            """
system_prompt: Test prompt.
tools:
  my_tool:
    description: Tool description.
"""
        )

        config = load_prompt_config(config_file)

        assert config.tools["my_tool"].description == "Tool description."
        assert config.tools["my_tool"].on_success is None

    def test_file_not_found(self) -> None:
        """Raise error for missing file."""
        with pytest.raises(PromptFileNotFoundError):
            load_prompt_config("/nonexistent/path.yaml")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Raise error for malformed YAML."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text(
            """
system_prompt: "unclosed string
  tools:
"""
        )

        with pytest.raises(PromptInvalidYAMLError):
            load_prompt_config(config_file)

    def test_missing_system_prompt(self, tmp_path: Path) -> None:
        """Raise error when system_prompt is missing."""
        config_file = tmp_path / "no_prompt.yaml"
        config_file.write_text(
            """
tools:
  my_tool:
    description: Tool description.
"""
        )

        with pytest.raises(PromptMissingKeyError) as exc_info:
            load_prompt_config(config_file)

        assert "system_prompt" in str(exc_info.value)

    def test_tool_missing_description(self, tmp_path: Path) -> None:
        """Raise error when tool is missing description."""
        config_file = tmp_path / "no_desc.yaml"
        config_file.write_text(
            """
system_prompt: Test prompt.
tools:
  my_tool:
    on_success: Something.
"""
        )

        with pytest.raises(PromptMissingKeyError) as exc_info:
            load_prompt_config(config_file)

        assert "description" in str(exc_info.value)

    def test_system_prompt_not_string(self, tmp_path: Path) -> None:
        """Raise error when system_prompt is not a string."""
        config_file = tmp_path / "list_prompt.yaml"
        config_file.write_text(
            """
system_prompt:
  - item1
  - item2
"""
        )

        with pytest.raises(PromptInvalidYAMLError):
            load_prompt_config(config_file)

    def test_tools_not_dict(self, tmp_path: Path) -> None:
        """Raise error when tools is not a dict."""
        config_file = tmp_path / "list_tools.yaml"
        config_file.write_text(
            """
system_prompt: Test prompt.
tools:
  - tool1
  - tool2
"""
        )

        with pytest.raises(PromptInvalidYAMLError):
            load_prompt_config(config_file)

    def test_tool_not_dict(self, tmp_path: Path) -> None:
        """Raise error when tool value is not a dict."""
        config_file = tmp_path / "string_tool.yaml"
        config_file.write_text(
            """
system_prompt: Test prompt.
tools:
  my_tool: "just a string"
"""
        )

        with pytest.raises(PromptInvalidYAMLError):
            load_prompt_config(config_file)

    def test_multiline_description(self, tmp_path: Path) -> None:
        """Multiline descriptions should preserve formatting."""
        config_file = tmp_path / "multiline.yaml"
        config_file.write_text(
            """
system_prompt: Test.
tools:
  my_tool:
    description: |
      Line 1.
      Line 2.
      Line 3.
"""
        )

        config = load_prompt_config(config_file)

        desc = config.tools["my_tool"].description
        assert "Line 1." in desc
        assert "Line 2." in desc
        assert "Line 3." in desc

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        """Accept Path object as input."""
        config_file = tmp_path / "path_test.yaml"
        config_file.write_text("system_prompt: Test.")

        config = load_prompt_config(config_file)  # Path object

        assert config.system_prompt == "Test."

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """Accept string path as input."""
        config_file = tmp_path / "string_test.yaml"
        config_file.write_text("system_prompt: Test.")

        config = load_prompt_config(str(config_file))  # String path

        assert config.system_prompt == "Test."


@pytest.mark.unit
class TestPromptConfigTypes:
    """Tests for PromptConfig and ToolPromptConfig types."""

    def test_tool_prompt_config_creation(self) -> None:
        """ToolPromptConfig can be created with required fields."""
        config = ToolPromptConfig(description="Test tool")

        assert config.description == "Test tool"
        assert config.on_success is None

    def test_tool_prompt_config_with_on_success(self) -> None:
        """ToolPromptConfig can have on_success."""
        config = ToolPromptConfig(description="Test tool", on_success="Follow up hint.")

        assert config.description == "Test tool"
        assert config.on_success == "Follow up hint."

    def test_prompt_config_creation(self) -> None:
        """PromptConfig can be created with required fields."""
        config = PromptConfig(system_prompt="You are helpful.")

        assert config.system_prompt == "You are helpful."
        assert config.tools == {}
        assert config.compact_summarize is None

    def test_prompt_config_with_all_fields(self) -> None:
        """PromptConfig can have all optional fields."""
        tools = {
            "tool1": ToolPromptConfig(description="Tool 1"),
            "tool2": ToolPromptConfig(description="Tool 2", on_success="Hint"),
        }
        config = PromptConfig(
            system_prompt="You are helpful.",
            tools=tools,
            compact_summarize="Summarize this.",
        )

        assert config.system_prompt == "You are helpful."
        assert len(config.tools) == 2
        assert config.compact_summarize == "Summarize this."
