"""The blocking run: `Agent.run` through the tool loop to a response and its stop reason."""

from unittest.mock import AsyncMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.tool_exec import execute_tool
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    ToolCall,
    Usage,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult
from tests.unit.agent.mocks import create_mock_router


@pytest.mark.unit
class TestAgentRun:
    """Test Agent.run execution."""

    @pytest.mark.asyncio
    async def test_simple_response_no_tools(self) -> None:
        """Agent should return response when LLM doesn't call tools."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            response = await agent.run(messages, stream=False)

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

        mock_client = AsyncMock(spec=BaseLLMClient)

        # First call: LLM requests tool call
        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("add"),
            arguments={"a": 2, "b": 3},
        )
        mock_client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=[tool_call]),
                usage=Usage(input_tokens=20, output_tokens=10),
                model="test-model",
            ),
            # Second call: LLM returns final response
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="The sum is 5."),
                usage=Usage(input_tokens=30, output_tokens=15),
                model="test-model",
            ),
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are a calculator.",
                tools=[add],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="What is 2 + 3?")]
            response = await agent.run(messages, stream=False)

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
        mock_client = AsyncMock(spec=BaseLLMClient)

        # LLM requests a tool that doesn't exist
        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("nonexistent"),
            arguments={},
        )
        mock_client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=[tool_call]),
                usage=Usage(input_tokens=10, output_tokens=5),
                model="test-model",
            ),
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="Tool not found."),
                usage=Usage(input_tokens=15, output_tokens=8),
                model="test-model",
            ),
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Do something")]
            response = await agent.run(messages, stream=False)

            assert len(response.tool_results) == 1
            assert response.tool_results[0].success is False
            assert "not found" in (response.tool_results[0].error or "")

    @pytest.mark.asyncio
    async def test_type_error_inside_tool_is_execution_failure(self) -> None:
        """A TypeError raised by the tool body is not an argument error (TG-7)."""

        @Tool(name="add", description="Add")
        async def add(a: int) -> ToolResult[int]:
            return ToolResult.ok(a + "1")  # type: ignore[operator]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            agent = Agent(config=AgentConfig(system_prompt="x", tools=[add]))
            call = ToolCall(
                id=ToolCallId("c1"), name=ToolName("add"), arguments={"a": 1}
            )
            result = await execute_tool(agent, call)

        assert result.success is False
        assert "failed" in (result.error or "")
        assert "Invalid arguments" not in (result.error or "")
        assert result.code == "tool_execution_failed"

    @pytest.mark.asyncio
    async def test_unbindable_arguments_are_invalid_arguments(self) -> None:
        """Arguments the signature cannot bind report as invalid (TG-7)."""

        @Tool(name="add", description="Add")
        async def add(a: int) -> ToolResult[int]:
            return ToolResult.ok(a)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            agent = Agent(config=AgentConfig(system_prompt="x", tools=[add]))
            call = ToolCall(
                id=ToolCallId("c1"), name=ToolName("add"), arguments={"b": 1}
            )
            result = await execute_tool(agent, call)

        assert result.success is False
        assert "Invalid arguments for tool 'add'" in (result.error or "")
        assert result.code == "tool_invalid_arguments"

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error(self) -> None:
        """Agent should handle tool exceptions gracefully."""

        @Tool(name="failing", description="A tool that fails")
        async def failing_tool() -> ToolResult[str]:
            raise RuntimeError("Something went wrong")

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("failing"),
            arguments={},
        )
        mock_client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=[tool_call]),
                usage=Usage(input_tokens=10, output_tokens=5),
                model="test-model",
            ),
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="Tool failed."),
                usage=Usage(input_tokens=15, output_tokens=8),
                model="test-model",
            ),
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[failing_tool],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Run the failing tool")]
            response = await agent.run(messages, stream=False)

            assert len(response.tool_results) == 1
            assert response.tool_results[0].success is False
            assert "failed" in (response.tool_results[0].error or "").lower()


@pytest.mark.unit
class TestAgentStopReason:
    """AgentResponse must surface the completion's stop_reason."""

    @pytest.mark.asyncio
    async def test_stop_reason_threaded_to_agent_response(self) -> None:
        """stop_reason from the final completion lands on AgentResponse."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Truncated..."),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
            stop_reason="max_tokens",
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            response = await agent.run(
                [Message(role=Role.USER, content="Hi")], stream=False
            )

            assert response.stop_reason == "max_tokens"
