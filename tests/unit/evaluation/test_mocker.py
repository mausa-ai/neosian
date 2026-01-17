"""Unit tests for evaluation mocker."""

from unittest.mock import MagicMock

import pytest

from neosian._foundation.evaluation.mocker import (
    create_mock_tool,
    mock_agent_tools,
)
from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.types import ToolCallCapture
from neosian._foundation.tools.base import Tool, ToolResult


class TestCreateMockTool:
    """Tests for create_mock_tool function."""

    @pytest.mark.asyncio
    async def test_mock_tool_returns_ok_result(self) -> None:
        """Test that mocked tool returns ToolResult.ok()."""

        @Tool(name="original_tool", description="Test tool")
        async def original_tool(param: str) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("real result")

        captures: list[ToolCallCapture] = []
        mock_tool = create_mock_tool(original_tool, captures)

        result = await mock_tool(param="test_value")

        assert result.success is True
        assert result.data == {
            "status": "success",
            "message": Evaluation.MOCK_SUCCESS_MESSAGE,
        }

    @pytest.mark.asyncio
    async def test_mock_tool_captures_call(self) -> None:
        """Test that mocked tool captures call arguments."""

        @Tool(name="original_tool", description="Test tool")
        async def original_tool(
            prompt: str, style: str = "default"  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("result")

        captures: list[ToolCallCapture] = []
        mock_tool = create_mock_tool(original_tool, captures)

        await mock_tool(prompt="a cat", style="realistic")

        assert len(captures) == 1
        assert captures[0].name == "original_tool"
        assert captures[0].arguments == {"prompt": "a cat", "style": "realistic"}

    @pytest.mark.asyncio
    async def test_mock_tool_preserves_metadata(self) -> None:
        """Test that mocked tool preserves _tool_metadata."""

        @Tool(name="my_tool", description="A test tool for testing")
        async def my_tool(x: int) -> ToolResult[int]:
            return ToolResult.ok(x * 2)

        captures: list[ToolCallCapture] = []
        mock_tool = create_mock_tool(my_tool, captures)

        assert hasattr(mock_tool, "_tool_metadata")
        assert mock_tool._tool_metadata.name == "my_tool"  # type: ignore[attr-defined]
        assert mock_tool._tool_metadata.description == "A test tool for testing"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_mock_tool_multiple_calls(self) -> None:
        """Test capturing multiple tool calls."""

        @Tool(name="tool", description="Test")
        async def tool(n: int) -> ToolResult[int]:
            return ToolResult.ok(n)

        captures: list[ToolCallCapture] = []
        mock_tool = create_mock_tool(tool, captures)

        await mock_tool(n=1)
        await mock_tool(n=2)
        await mock_tool(n=3)

        assert len(captures) == 3
        assert captures[0].arguments == {"n": 1}
        assert captures[1].arguments == {"n": 2}
        assert captures[2].arguments == {"n": 3}


class TestMockAgentTools:
    """Tests for mock_agent_tools function."""

    def test_mock_agent_tools_replaces_tools(self) -> None:
        """Test that mock_agent_tools replaces all agent tools."""

        @Tool(name="tool_a", description="Tool A")
        async def tool_a(x: int) -> ToolResult[int]:
            return ToolResult.ok(x)

        @Tool(name="tool_b", description="Tool B")
        async def tool_b(y: str) -> ToolResult[str]:
            return ToolResult.ok(y)

        # Create mock agent with _tools dict
        mock_agent = MagicMock()
        mock_agent._tools = {"tool_a": tool_a, "tool_b": tool_b}

        captures: list[ToolCallCapture] = []
        mock_agent_tools(mock_agent, captures)

        # Verify tools were replaced
        assert mock_agent._tools["tool_a"] is not tool_a
        assert mock_agent._tools["tool_b"] is not tool_b

    @pytest.mark.asyncio
    async def test_mock_agent_tools_shared_captures(self) -> None:
        """Test that all mocked tools share the same captures list."""

        @Tool(name="tool_a", description="Tool A")
        async def tool_a(x: int) -> ToolResult[int]:
            return ToolResult.ok(x)

        @Tool(name="tool_b", description="Tool B")
        async def tool_b(y: str) -> ToolResult[str]:
            return ToolResult.ok(y)

        mock_agent = MagicMock()
        mock_agent._tools = {"tool_a": tool_a, "tool_b": tool_b}

        captures: list[ToolCallCapture] = []
        mock_agent_tools(mock_agent, captures)

        # Call both mocked tools
        await mock_agent._tools["tool_a"](x=42)
        await mock_agent._tools["tool_b"](y="hello")

        assert len(captures) == 2
        assert captures[0].name == "tool_a"
        assert captures[0].arguments == {"x": 42}
        assert captures[1].name == "tool_b"
        assert captures[1].arguments == {"y": "hello"}

    def test_mock_agent_tools_preserves_metadata(self) -> None:
        """Test that mocking preserves tool metadata for LLM schema."""

        @Tool(name="generate_image", description="Generate an image")
        async def generate_image(prompt: str) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("url")

        mock_agent = MagicMock()
        mock_agent._tools = {"generate_image": generate_image}

        captures: list[ToolCallCapture] = []
        mock_agent_tools(mock_agent, captures)

        mocked = mock_agent._tools["generate_image"]
        assert hasattr(mocked, "_tool_metadata")
        assert mocked._tool_metadata.name == "generate_image"
        assert mocked._tool_metadata.description == "Generate an image"

    def test_mock_agent_tools_empty_tools(self) -> None:
        """Test mocking agent with no tools."""
        mock_agent = MagicMock()
        mock_agent._tools = {}

        captures: list[ToolCallCapture] = []
        mock_agent_tools(mock_agent, captures)

        assert mock_agent._tools == {}
        assert captures == []
