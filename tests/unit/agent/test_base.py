"""Tests for Agent core."""

from unittest.mock import AsyncMock

import pytest

from neosian._foundation.agent.base import Agent, AgentResponse
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    ToolCall,
    Usage,
)
from neosian._foundation.shared.types import ModelId, SystemPrompt, ToolCallId, ToolName
from neosian._foundation.tools.base import Tool, ToolResult


@pytest.mark.unit
class TestAgentInit:
    """Test Agent initialization."""

    def test_agent_init_without_tools(self) -> None:
        """Agent should initialize without tools."""
        client = AsyncMock(spec=BaseLLMClient)
        agent = Agent(
            client=client,
            model=ModelId("test-model"),
            system_prompt=SystemPrompt("You are helpful."),
        )
        assert agent._model == "test-model"
        assert agent._system_prompt == "You are helpful."
        assert len(agent._tools) == 0

    def test_agent_init_with_tools(self) -> None:
        """Agent should register tools from decorated functions."""

        @Tool(name="greet", description="Greet someone")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello, {name}!")

        client = AsyncMock(spec=BaseLLMClient)
        agent = Agent(
            client=client,
            model=ModelId("test-model"),
            system_prompt=SystemPrompt("You are helpful."),
            tools=[greet],
        )
        assert "greet" in agent._tools
        assert len(agent._tool_definitions) == 1

    def test_agent_rejects_non_tool_functions(self) -> None:
        """Agent should reject functions without @Tool decorator."""

        async def not_a_tool(x: int) -> int:
            return x * 2

        client = AsyncMock(spec=BaseLLMClient)
        with pytest.raises(ValueError, match="not decorated with @Tool"):
            Agent(
                client=client,
                model=ModelId("test-model"),
                system_prompt=SystemPrompt("You are helpful."),
                tools=[not_a_tool],  # type: ignore[list-item]
            )


@pytest.mark.unit
class TestAgentRun:
    """Test Agent.run execution."""

    @pytest.mark.asyncio
    async def test_simple_response_no_tools(self) -> None:
        """Agent should return response when LLM doesn't call tools."""
        client = AsyncMock(spec=BaseLLMClient)
        client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model=ModelId("test-model"),
        )

        agent = Agent(
            client=client,
            model=ModelId("test-model"),
            system_prompt=SystemPrompt("You are helpful."),
        )

        messages = [Message(role=Role.USER, content="Hi")]
        response = await agent.run(messages)

        assert response.message.content == "Hello!"
        assert response.message.role == Role.ASSISTANT
        assert len(response.tool_calls_made) == 0
        assert response.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_tool_execution_loop(self) -> None:
        """Agent should execute tools and continue conversation."""

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:  # noqa: ARG001
            return ToolResult.ok(a + b)

        client = AsyncMock(spec=BaseLLMClient)

        # First call: LLM requests tool call
        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("add"),
            arguments={"a": 2, "b": 3},
        )
        client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=[tool_call]),
                usage=Usage(input_tokens=20, output_tokens=10),
                model=ModelId("test-model"),
            ),
            # Second call: LLM returns final response
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="The sum is 5."),
                usage=Usage(input_tokens=30, output_tokens=15),
                model=ModelId("test-model"),
            ),
        ]

        agent = Agent(
            client=client,
            model=ModelId("test-model"),
            system_prompt=SystemPrompt("You are a calculator."),
            tools=[add],
        )

        messages = [Message(role=Role.USER, content="What is 2 + 3?")]
        response = await agent.run(messages)

        assert response.message.content == "The sum is 5."
        assert len(response.tool_calls_made) == 1
        assert response.tool_calls_made[0].name == "add"
        assert len(response.tool_results) == 1
        assert response.tool_results[0].success is True
        assert response.tool_results[0].data == 5
        assert response.usage.total_tokens == 75  # 20+10+30+15

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """Agent should handle unknown tool gracefully."""
        client = AsyncMock(spec=BaseLLMClient)

        # LLM requests a tool that doesn't exist
        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("nonexistent"),
            arguments={},
        )
        client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=[tool_call]),
                usage=Usage(input_tokens=10, output_tokens=5),
                model=ModelId("test-model"),
            ),
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="Tool not found."),
                usage=Usage(input_tokens=15, output_tokens=8),
                model=ModelId("test-model"),
            ),
        ]

        agent = Agent(
            client=client,
            model=ModelId("test-model"),
            system_prompt=SystemPrompt("You are helpful."),
        )

        messages = [Message(role=Role.USER, content="Do something")]
        response = await agent.run(messages)

        assert len(response.tool_results) == 1
        assert response.tool_results[0].success is False
        assert "not found" in (response.tool_results[0].error or "")

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error(self) -> None:
        """Agent should handle tool exceptions gracefully."""

        @Tool(name="failing", description="A tool that fails")
        async def failing_tool() -> ToolResult[str]:
            raise RuntimeError("Something went wrong")

        client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("failing"),
            arguments={},
        )
        client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=[tool_call]),
                usage=Usage(input_tokens=10, output_tokens=5),
                model=ModelId("test-model"),
            ),
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="Tool failed."),
                usage=Usage(input_tokens=15, output_tokens=8),
                model=ModelId("test-model"),
            ),
        ]

        agent = Agent(
            client=client,
            model=ModelId("test-model"),
            system_prompt=SystemPrompt("You are helpful."),
            tools=[failing_tool],
        )

        messages = [Message(role=Role.USER, content="Run the failing tool")]
        response = await agent.run(messages)

        assert len(response.tool_results) == 1
        assert response.tool_results[0].success is False
        assert "failed" in (response.tool_results[0].error or "").lower()


@pytest.mark.unit
class TestAgentResponse:
    """Test AgentResponse dataclass."""

    def test_agent_response_defaults(self) -> None:
        """AgentResponse should have sensible defaults."""
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="Hello"),
        )
        assert response.message.content == "Hello"
        assert response.tool_calls_made == []
        assert response.tool_results == []
        assert response.usage.total_tokens == 0
