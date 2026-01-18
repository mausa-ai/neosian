"""Tests for type definitions."""

import pytest

from neosian._foundation.shared.exceptions import InvalidModelError
from neosian._foundation.shared.types import (
    AgentConfig,
    AgentName,
    Model,
    Provider,
    SystemPrompt,
    ToolName,
)


@pytest.mark.unit
class TestTypeDefinitions:
    """Test that NewType definitions work correctly."""

    def test_newtypes_are_str_compatible(self) -> None:
        """NewTypes should be string-compatible for runtime use."""
        agent = AgentName("test-agent")
        tool = ToolName("my_tool")

        # Should work as strings
        assert agent.startswith("test")
        assert "_" in tool


@pytest.mark.unit
class TestProviderEnum:
    """Test Provider enum."""

    def test_provider_values(self) -> None:
        """Provider enum should have expected values."""
        assert Provider.GROQ.value == "groq"
        assert Provider.OPENAI.value == "openai"
        assert Provider.ANTHROPIC.value == "anthropic"

    def test_provider_is_string_compatible(self) -> None:
        """Provider should be string-compatible."""
        # Direct comparison works due to str, Enum inheritance
        assert Provider.GROQ == "groq"
        # Can be used in string operations
        assert f"provider: {Provider.GROQ.value}" == "provider: groq"


@pytest.mark.unit
class TestModelEnum:
    """Test Model enum."""

    def test_model_values(self) -> None:
        """Model enum should have expected values."""
        assert Model.GPT_OSS_20B.value == "openai/gpt-oss-20b"
        assert Model.LLAMA_3_3_70B.value == "llama-3.3-70b-versatile"
        assert Model.GPT_5_NANO.value == "gpt-5-nano-2025-08-07"
        assert Model.CLAUDE_SONNET_4_5.value == "claude-sonnet-4-5-20250929"

    def test_model_is_string_compatible(self) -> None:
        """Model should be string-compatible."""
        # Direct comparison works due to str, Enum inheritance
        assert Model.GPT_OSS_20B == "openai/gpt-oss-20b"
        # Can be used in string operations
        assert f"model: {Model.GPT_OSS_20B.value}" == "model: openai/gpt-oss-20b"

    def test_model_provider_property(self) -> None:
        """Model should have provider property."""
        assert Model.GPT_OSS_20B.provider == Provider.GROQ
        assert Model.LLAMA_3_3_70B.provider == Provider.GROQ
        assert Model.GPT_5_NANO.provider == Provider.OPENAI
        assert Model.CLAUDE_SONNET_4_5.provider == Provider.ANTHROPIC

    def test_model_max_output_tokens_property(self) -> None:
        """Model should have max_output_tokens property."""
        # Default is 8192
        assert Model.GPT_OSS_20B.max_output_tokens == 8192
        assert Model.LLAMA_3_3_70B.max_output_tokens == 8192
        assert Model.GPT_5_NANO.max_output_tokens == 8192

        # Claude models have 65536
        assert Model.CLAUDE_SONNET_4_5.max_output_tokens == 65536
        assert Model.CLAUDE_OPUS_4_5.max_output_tokens == 65536
        assert Model.CLAUDE_HAIKU_4_5.max_output_tokens == 65536


@pytest.mark.unit
class TestAgentConfigValidation:
    """Test AgentConfig model validation."""

    def test_valid_model_accepted(self) -> None:
        """AgentConfig should accept valid Model enum values."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GPT_OSS_20B,
        )
        assert config.model == Model.GPT_OSS_20B

    def test_default_model(self) -> None:
        """AgentConfig should use default model when not specified."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
        )
        assert config.model == Model.GPT_OSS_20B

    def test_invalid_model_string_raises_error(self) -> None:
        """AgentConfig should raise InvalidModelError for string models."""
        with pytest.raises(InvalidModelError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model="gpt-4o",  # type: ignore[arg-type]
            )

        assert exc_info.value.model_value == "gpt-4o"
        assert "str" in str(exc_info.value)
        assert "Model.GPT_OSS_20B" in str(exc_info.value)

    def test_invalid_model_int_raises_error(self) -> None:
        """AgentConfig should raise InvalidModelError for non-string types."""
        with pytest.raises(InvalidModelError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=123,  # type: ignore[arg-type]
            )

        assert exc_info.value.model_value == 123
        assert "int" in str(exc_info.value)

    def test_error_message_lists_all_models(self) -> None:
        """InvalidModelError should list all supported models."""
        with pytest.raises(InvalidModelError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model="invalid",  # type: ignore[arg-type]
            )

        error_msg = str(exc_info.value)
        # Check that all models are listed
        for model in Model:
            assert f"Model.{model.name}" in error_msg
