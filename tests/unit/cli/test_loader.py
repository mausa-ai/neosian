"""Unit tests for agent loader."""

import tempfile
from pathlib import Path

import pytest

from neosian._cli.loader import AgentDefinition, load_agent_definition
from neosian._foundation.shared.exceptions import (
    AgentFileNotFoundError,
    AgentInvalidDefinitionError,
    AgentMissingSystemPromptError,
    AgentMissingToolsError,
)
from neosian._foundation.tools.base import Tool, ToolResult


@pytest.mark.unit
class TestLoadAgentDefinition:
    """Tests for load_agent_definition function."""

    def test_loads_valid_agent_file(self) -> None:
        """Test loading a valid agent file."""
        agent_code = '''
from neosian._foundation.tools.base import Tool, ToolResult

system_prompt = "You are a helpful assistant."

@Tool(name="greet", description="Greet someone")
async def greet(name: str) -> ToolResult[str]:
    return ToolResult.ok(f"Hello, {name}!")

tools = [greet]
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(agent_code)
            f.flush()

            definition = load_agent_definition(f.name)

            assert isinstance(definition, AgentDefinition)
            assert definition.system_prompt == "You are a helpful assistant."
            assert len(definition.tools) == 1
            assert definition.name == Path(f.name).stem

    def test_raises_on_file_not_found(self) -> None:
        """Test error when file doesn't exist."""
        with pytest.raises(AgentFileNotFoundError):
            load_agent_definition("/nonexistent/path/agent.py")

    def test_raises_on_missing_system_prompt(self) -> None:
        """Test error when system_prompt is not defined."""
        agent_code = '''
tools = []
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentMissingSystemPromptError):
                load_agent_definition(f.name)

    def test_raises_on_missing_tools(self) -> None:
        """Test error when tools is not defined."""
        agent_code = '''
system_prompt = "You are helpful."
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentMissingToolsError):
                load_agent_definition(f.name)

    def test_raises_on_invalid_system_prompt_type(self) -> None:
        """Test error when system_prompt is not a string."""
        agent_code = '''
system_prompt = 123
tools = []
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentInvalidDefinitionError):
                load_agent_definition(f.name)

    def test_raises_on_invalid_tools_type(self) -> None:
        """Test error when tools is not a list."""
        agent_code = '''
system_prompt = "Hello"
tools = "not a list"
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentInvalidDefinitionError):
                load_agent_definition(f.name)

    def test_raises_on_syntax_error(self) -> None:
        """Test error when file has syntax errors."""
        agent_code = '''
system_prompt = "Hello
'''  # Missing closing quote
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentInvalidDefinitionError):
                load_agent_definition(f.name)

    def test_empty_tools_list_is_valid(self) -> None:
        """Test that an empty tools list is valid."""
        agent_code = '''
system_prompt = "You are helpful."
tools = []
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(agent_code)
            f.flush()

            definition = load_agent_definition(f.name)

            assert definition.tools == []


@pytest.mark.unit
class TestAgentDefinition:
    """Tests for AgentDefinition dataclass."""

    def test_agent_definition_fields(self) -> None:
        """Test AgentDefinition has correct fields."""

        @Tool(name="test", description="Test tool")
        async def test_tool() -> ToolResult[str]:
            return ToolResult.ok("test")

        definition = AgentDefinition(
            system_prompt="Test prompt",
            tools=[test_tool],
            name="test_agent",
        )

        assert definition.system_prompt == "Test prompt"
        assert len(definition.tools) == 1
        assert definition.name == "test_agent"