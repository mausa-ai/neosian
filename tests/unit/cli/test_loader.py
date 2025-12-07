"""Unit tests for agent loader."""

import tempfile
from pathlib import Path

import pytest

from neosian._cli.loader import load_agent_config
from neosian._foundation.shared.exceptions import (
    AgentFileNotFoundError,
    AgentInvalidConfigurationError,
    AgentInvalidDefinitionError,
    AgentMissingConfigurationError,
)
from neosian._foundation.shared.types import AgentConfig
from neosian._foundation.tools.base import Tool, ToolResult


@pytest.mark.unit
class TestLoadAgentConfig:
    """Tests for load_agent_config function."""

    def test_loads_valid_agent_file(self) -> None:
        """Test loading a valid agent file with configuration."""
        agent_code = """
from neosian import AgentConfig, Tool, ToolResult

@Tool(name="greet", description="Greet someone")
async def greet(name: str) -> ToolResult[str]:
    return ToolResult.ok(f"Hello, {name}!")

configuration = AgentConfig(
    system_prompt="You are a helpful assistant.",
    tools=[greet],
)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            config, name = load_agent_config(f.name)

            assert isinstance(config, AgentConfig)
            assert config.system_prompt == "You are a helpful assistant."
            assert len(config.tools) == 1
            assert name == Path(f.name).stem

    def test_raises_on_file_not_found(self) -> None:
        """Test error when file doesn't exist."""
        with pytest.raises(AgentFileNotFoundError):
            load_agent_config("/nonexistent/path/agent.py")

    def test_raises_on_missing_configuration(self) -> None:
        """Test error when configuration is not defined."""
        agent_code = """
# No configuration variable defined
x = 1
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentMissingConfigurationError):
                load_agent_config(f.name)

    def test_raises_on_invalid_configuration_type(self) -> None:
        """Test error when configuration is not an AgentConfig."""
        agent_code = """
configuration = "not an AgentConfig"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentInvalidConfigurationError):
                load_agent_config(f.name)

    def test_raises_on_syntax_error(self) -> None:
        """Test error when file has syntax errors."""
        agent_code = """
configuration = "Hello
"""  # Missing closing quote
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentInvalidDefinitionError):
                load_agent_config(f.name)

    def test_empty_tools_list_is_valid(self) -> None:
        """Test that an empty tools list is valid."""
        agent_code = """
from neosian import AgentConfig

configuration = AgentConfig(
    system_prompt="You are helpful.",
    tools=[],
)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            config, _ = load_agent_config(f.name)

            assert config.tools == []

    def test_loads_optional_provider_and_model(self) -> None:
        """Test loading configuration with provider and model."""
        agent_code = """
from neosian import AgentConfig

configuration = AgentConfig(
    system_prompt="You are helpful.",
    tools=[],
    provider="openai",
    model="gpt-4o",
)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            config, _ = load_agent_config(f.name)

            assert config.provider == "openai"
            assert config.model == "gpt-4o"


@pytest.mark.unit
class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_agent_config_fields(self) -> None:
        """Test AgentConfig has correct fields."""

        @Tool(name="test", description="Test tool")
        async def test_tool() -> ToolResult[str]:
            return ToolResult.ok("test")

        config = AgentConfig(
            system_prompt="Test prompt",
            tools=[test_tool],
        )

        assert config.system_prompt == "Test prompt"
        assert len(config.tools) == 1
        assert config.provider is None
        assert config.model is None
        assert config.enable_todo is True

    def test_agent_config_with_all_fields(self) -> None:
        """Test AgentConfig with all optional fields."""
        config = AgentConfig(
            system_prompt="Test prompt",
            tools=[],
            provider="groq",
            model="llama-3.3-70b-versatile",
            enable_todo=False,
        )

        assert config.system_prompt == "Test prompt"
        assert config.tools == []
        assert config.provider == "groq"
        assert config.model == "llama-3.3-70b-versatile"
        assert config.enable_todo is False
