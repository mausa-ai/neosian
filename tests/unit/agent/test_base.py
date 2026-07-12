"""Tests for Agent core."""

import asyncio
import json
import time
import weakref
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.agent.base import Agent, AgentResponse
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    StreamChunk,
    ToolCall,
    Usage,
)
from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.types import (
    AgentConfig,
    FallbackConfig,
    Model,
    Provider,
    ReasoningEffort,
    SystemPrompt,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult


def _parse_sse(sse: str) -> tuple[str, dict[str, Any]]:
    """Parse one SSE string into (event_type, data_dict)."""
    lines = sse.strip().split("\n")
    event_type = lines[0].removeprefix("event: ")
    data = json.loads(lines[1].removeprefix("data: "))
    return event_type, data


def _create_mock_router(mock_client: BaseLLMClient | None = None) -> MagicMock:
    """Create a mock ProviderRouter that returns the given client.

    Args:
        mock_client: The mock client to return. If None, creates a new AsyncMock.

    Returns:
        MagicMock configured as a ProviderRouter.
    """
    if mock_client is None:
        mock_client = AsyncMock(spec=BaseLLMClient)

    mock_router = MagicMock()
    mock_router.has_provider.return_value = True
    mock_router.create_client.return_value = mock_client
    return mock_router


@pytest.mark.unit
class TestAgentInit:
    """Test Agent initialization."""

    def test_agent_init_without_tools(self) -> None:
        """Agent should initialize without tools."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert agent._system_prompt == "You are helpful."
            assert len(agent._tools) == 0

    def test_agent_init_with_todo_enabled_by_default(self) -> None:
        """Agent should include todo tool by default."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
            )
            agent = Agent(config=config)

            assert "update_todo" in agent._tools
            assert len(agent._tools) == 1

    def test_agent_init_todo_disabled(self) -> None:
        """Agent should not include todo tool when disabled."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert "update_todo" not in agent._tools
            assert len(agent._tools) == 0

    def test_agent_init_with_tools(self) -> None:
        """Agent should register tools from decorated functions."""

        @Tool(name="greet", description="Greet someone")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello, {name}!")

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[greet],
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert "greet" in agent._tools
            assert len(agent._tool_definitions) == 1

    def test_agent_init_with_tools_and_todo(self) -> None:
        """Agent should register both user tools and todo tool."""

        @Tool(name="greet", description="Greet someone")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello, {name}!")

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[greet],
                enable_todo=True,
            )
            agent = Agent(config=config)

            assert "greet" in agent._tools
            assert "update_todo" in agent._tools
            assert len(agent._tool_definitions) == 2

    def test_agent_rejects_non_tool_functions(self) -> None:
        """Agent should reject functions without @Tool decorator."""

        async def not_a_tool(x: int) -> int:
            return x * 2

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[not_a_tool],  # type: ignore[list-item]
                enable_todo=False,
            )

            with pytest.raises(ValueError, match="not decorated with @Tool"):
                Agent(config=config)


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
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
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
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a calculator."),
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
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Do something")]
            response = await agent.run(messages, stream=False)

            assert len(response.tool_results) == 1
            assert response.tool_results[0].success is False
            assert "not found" in (response.tool_results[0].error or "")

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
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
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


@pytest.mark.unit
class TestAgentRunStreaming:
    """Test Agent.run with stream=True."""

    @pytest.mark.asyncio
    async def test_streaming_simple_response(self) -> None:
        """Agent should yield SSE events when streaming without tools."""
        mock_client = AsyncMock(spec=BaseLLMClient)

        # stream() yields content chunks directly (no complete() in streaming path)
        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(content="Hello ")
            yield StreamChunk(content="world!")
            yield StreamChunk(finish_reason="stop")

        mock_client.stream = mock_stream

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            result = await agent.run(messages, stream=True)

            # Collect all SSE events
            events = []
            async for sse in result:
                events.append(sse)

            # Should have content events and done event
            assert len(events) == 3
            assert "content" in events[0]
            assert "Hello " in events[0]
            assert "world!" in events[1]
            assert "done" in events[2]

    @pytest.mark.asyncio
    async def test_streaming_with_tool_calls(self) -> None:
        """Agent should yield tool call and result SSE events when streaming."""

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("add"),
            arguments={"a": 2, "b": 3},
        )

        # Stateful stream: first call returns tool calls, second returns content
        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First iteration: tool call
                yield StreamChunk(tool_calls=[tool_call], finish_reason="tool_calls")
            else:
                # Second iteration: final content
                yield StreamChunk(content="The sum is 5.")
                yield StreamChunk(finish_reason="stop")

        mock_client.stream = mock_stream

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a calculator."),
                tools=[add],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="What is 2 + 3?")]
            result = await agent.run(messages, stream=True)

            events = []
            async for sse in result:
                events.append(sse)

            # Should have: tool_call, tool_result, content, done
            assert len(events) == 4

            # First event should be tool call
            assert "tool_call" in events[0]
            assert "add" in events[0]

            # Second event should be tool result
            assert "tool_result" in events[1]
            assert "success" in events[1]

            # Third event should be content
            assert "content" in events[2]
            assert "The sum is 5." in events[2]

            # Fourth event should be done
            assert "done" in events[3]

    @pytest.mark.asyncio
    async def test_streaming_returns_async_iterator(self) -> None:
        """Agent.run(stream=True) should return an AsyncIterator."""
        mock_client = AsyncMock(spec=BaseLLMClient)

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(content="Hi")
            yield StreamChunk(finish_reason="stop")

        mock_client.stream = mock_stream

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            result = await agent.run(messages, stream=True)

            # Result should be an async iterator
            assert hasattr(result, "__anext__")


@pytest.mark.unit
class TestAgentHeartbeats:
    """Test Agent heartbeat functionality during tool execution."""

    @pytest.mark.asyncio
    async def test_fast_tool_no_heartbeats(self) -> None:
        """Fast tool execution should not emit heartbeats."""

        @Tool(name="fast", description="A fast tool")
        async def fast_tool() -> ToolResult[str]:
            return ToolResult.ok("done")

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("fast"),
            arguments={},
        )

        # Stateful stream: first call returns tool call, second returns content
        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=[tool_call], finish_reason="tool_calls")
            else:
                yield StreamChunk(content="Done!")
                yield StreamChunk(finish_reason="stop")

        mock_client.stream = mock_stream

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[fast_tool],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Run fast tool")]
            result = await agent.run(messages, stream=True)

            events = []
            async for sse in result:
                events.append(sse)

            # Should have: tool_call, tool_result, content, done (no heartbeats)
            heartbeat_events = [e for e in events if "heartbeat" in e]
            assert len(heartbeat_events) == 0

    @pytest.mark.asyncio
    async def test_slow_tool_emits_heartbeats(self) -> None:
        """Slow tool execution should emit heartbeat events."""
        import asyncio

        @Tool(name="slow", description="A slow tool")
        async def slow_tool() -> ToolResult[str]:
            # Sleep longer than heartbeat interval (mocked to 0.05s)
            await asyncio.sleep(0.15)
            return ToolResult.ok("finally done")

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_slow"),
            name=ToolName("slow"),
            arguments={},
        )

        # Stateful stream: first call returns tool call, second returns content
        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=[tool_call], finish_reason="tool_calls")
            else:
                yield StreamChunk(content="Finished!")
                yield StreamChunk(finish_reason="stop")

        mock_client.stream = mock_stream

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                0.05,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[slow_tool],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Run slow tool")]
            result = await agent.run(messages, stream=True)

            events = []
            async for sse in result:
                events.append(sse)

            # Should have heartbeat events
            heartbeat_events = [e for e in events if "heartbeat" in e]
            assert len(heartbeat_events) >= 1

            # Verify heartbeat contains tool_call_id and elapsed_seconds
            assert "call_slow" in heartbeat_events[0]
            assert "elapsed_seconds" in heartbeat_events[0]

    @pytest.mark.asyncio
    async def test_heartbeat_contains_correct_tool_call_id(self) -> None:
        """Heartbeat events should contain the correct tool_call_id."""
        import asyncio

        @Tool(name="delayed", description="A delayed tool")
        async def delayed_tool() -> ToolResult[str]:
            await asyncio.sleep(0.08)
            return ToolResult.ok("done")

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("unique_id_123"),
            name=ToolName("delayed"),
            arguments={},
        )

        # Stateful stream: first call returns tool call, second returns content
        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=[tool_call], finish_reason="tool_calls")
            else:
                yield StreamChunk(content="Done!")
                yield StreamChunk(finish_reason="stop")

        mock_client.stream = mock_stream

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                0.03,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[delayed_tool],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Run delayed tool")]
            result = await agent.run(messages, stream=True)

            events = []
            async for sse in result:
                events.append(sse)

            heartbeat_events = [e for e in events if "heartbeat" in e]
            assert len(heartbeat_events) >= 1

            # Verify the tool_call_id is in the heartbeat
            assert "unique_id_123" in heartbeat_events[0]


@pytest.mark.unit
class TestAgentReasoningEffort:
    """Test Agent reasoning_effort handling."""

    @pytest.mark.asyncio
    async def test_agent_stores_reasoning_effort(self) -> None:
        """Agent should store reasoning_effort from config."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_GPT_OSS_20B,
                reasoning_effort=ReasoningEffort.HIGH,
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert agent._reasoning_effort == ReasoningEffort.HIGH

    @pytest.mark.asyncio
    async def test_agent_passes_reasoning_effort_to_complete(self) -> None:
        """Agent should pass reasoning_effort to LLM complete() calls."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="openai/gpt-oss-20b",
        )

        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_GPT_OSS_20B,
                reasoning_effort=ReasoningEffort.HIGH,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Think about this.")]
            await agent.run(messages, stream=False)

            # Verify reasoning_effort was passed to complete()
            mock_client.complete.assert_called_once()
            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["reasoning_effort"] == ReasoningEffort.HIGH

    @pytest.mark.asyncio
    async def test_agent_none_reasoning_effort_passed_as_none(self) -> None:
        """Agent should pass None for reasoning_effort when not configured."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="openai/gpt-oss-20b",
        )

        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_GPT_OSS_20B,
                reasoning_effort=None,  # Explicitly None
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            await agent.run(messages, stream=False)

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["reasoning_effort"] is None

    @pytest.mark.asyncio
    async def test_fallback_silently_drops_reasoning_for_non_supporting_model(
        self,
    ) -> None:
        """Agent should silently drop reasoning_effort when fallback model doesn't support it."""
        # Main model client that fails
        mock_main_client = AsyncMock(spec=BaseLLMClient)
        mock_main_client.complete.side_effect = Exception("Main model failed")

        # Fallback model client that succeeds
        mock_fallback_client = AsyncMock(spec=BaseLLMClient)
        mock_fallback_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Fallback response"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="llama-3.3-70b-versatile",
        )

        # Router that returns different clients based on provider
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True

        def create_client(provider: Provider) -> AsyncMock:
            if provider == Provider.GROQ:
                # Both are Groq, but we need to distinguish by model
                # The first call is for main model, subsequent for fallback
                if mock_router.create_client.call_count <= 1:
                    return mock_main_client
                return mock_fallback_client
            return mock_fallback_client

        mock_router.create_client.side_effect = create_client

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_GPT_OSS_20B,  # Supports reasoning
                reasoning_effort=ReasoningEffort.HIGH,
                fallback=FallbackConfig(model=Model.GROQ_LLAMA_3_3_70B),  # No reasoning
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            response = await agent.run(messages, stream=False)

            # Response should be from fallback
            assert response.message.content == "Fallback response"

            # Main client should have been called with reasoning_effort
            main_call_kwargs = mock_main_client.complete.call_args.kwargs
            assert main_call_kwargs["reasoning_effort"] == ReasoningEffort.HIGH

            # Fallback client should have been called with None (silently dropped)
            fallback_call_kwargs = mock_fallback_client.complete.call_args.kwargs
            assert fallback_call_kwargs["reasoning_effort"] is None

    @pytest.mark.asyncio
    async def test_main_model_keeps_reasoning_when_supported(self) -> None:
        """Agent should keep reasoning_effort when main model supports it."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content="Reasoned response",
                reasoning="I thought about this...",
            ),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="openai/gpt-oss-20b",
        )

        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_GPT_OSS_20B,
                reasoning_effort=ReasoningEffort.MEDIUM,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Think about this")]
            response = await agent.run(messages, stream=False)

            assert response.message.content == "Reasoned response"
            assert response.message.reasoning == "I thought about this..."

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["reasoning_effort"] == ReasoningEffort.MEDIUM


@pytest.mark.unit
class TestAgentParallelToolExecution:
    """Test parallel dispatch of tool calls from a single assistant turn."""

    @pytest.mark.asyncio
    async def test_blocking_parallel_wall_clock(self) -> None:
        """Three tools at 300/100/200ms must finish in ~max, not sum."""

        @Tool(name="t_a", description="A")
        async def t_a() -> ToolResult[str]:
            await asyncio.sleep(0.3)
            return ToolResult.ok("a")

        @Tool(name="t_b", description="B")
        async def t_b() -> ToolResult[str]:
            await asyncio.sleep(0.1)
            return ToolResult.ok("b")

        @Tool(name="t_c", description="C")
        async def t_c() -> ToolResult[str]:
            await asyncio.sleep(0.2)
            return ToolResult.ok("c")

        mock_client = AsyncMock(spec=BaseLLMClient)
        tool_calls = [
            ToolCall(id=ToolCallId("id_a"), name=ToolName("t_a"), arguments={}),
            ToolCall(id=ToolCallId("id_b"), name=ToolName("t_b"), arguments={}),
            ToolCall(id=ToolCallId("id_c"), name=ToolName("t_c"), arguments={}),
        ]
        mock_client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=tool_calls),
                usage=Usage(input_tokens=10, output_tokens=5),
                model="test-model",
            ),
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="done"),
                usage=Usage(input_tokens=20, output_tokens=5),
                model="test-model",
            ),
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[t_a, t_b, t_c],
                enable_todo=False,
            )
            agent = Agent(config=config)
            messages = [Message(role=Role.USER, content="go")]

            start = time.monotonic()
            response = await agent.run(messages, stream=False)
            elapsed = time.monotonic() - start

            # Parallel ~0.3s; sequential would be ~0.6s. 0.5s gives CI slack.
            assert elapsed < 0.5, f"expected parallel execution, took {elapsed:.3f}s"

            assert [tc.name for tc in response.tool_calls_made] == ["t_a", "t_b", "t_c"]
            assert [r.data for r in response.tool_results] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_streaming_emits_results_in_completion_order(self) -> None:
        """tool_call events: submission order. tool_result events: completion order."""

        @Tool(name="slow", description="slow")
        async def slow() -> ToolResult[str]:
            await asyncio.sleep(0.2)
            return ToolResult.ok("slow")

        @Tool(name="fast", description="fast")
        async def fast() -> ToolResult[str]:
            await asyncio.sleep(0.05)
            return ToolResult.ok("fast")

        @Tool(name="mid", description="mid")
        async def mid() -> ToolResult[str]:
            await asyncio.sleep(0.1)
            return ToolResult.ok("mid")

        tool_calls = [
            ToolCall(id=ToolCallId("id_slow"), name=ToolName("slow"), arguments={}),
            ToolCall(id=ToolCallId("id_fast"), name=ToolName("fast"), arguments={}),
            ToolCall(id=ToolCallId("id_mid"), name=ToolName("mid"), arguments={}),
        ]

        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=tool_calls, finish_reason="tool_calls")
            else:
                yield StreamChunk(content="done")
                yield StreamChunk(finish_reason="stop")

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.stream = mock_stream

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                30.0,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[slow, fast, mid],
                enable_todo=False,
            )
            agent = Agent(config=config)
            messages = [Message(role=Role.USER, content="go")]
            stream = await agent.run(messages, stream=True)
            parsed = [_parse_sse(s) async for s in stream]

        call_events = [(et, d) for et, d in parsed if et == "tool_call"]
        result_events = [(et, d) for et, d in parsed if et == "tool_result"]

        assert [d["id"] for _, d in call_events] == ["id_slow", "id_fast", "id_mid"]
        assert [d["tool_call_id"] for _, d in result_events] == [
            "id_fast",
            "id_mid",
            "id_slow",
        ]

        # All tool_call events must precede any tool_result event.
        first_result_idx = next(
            i for i, (et, _) in enumerate(parsed) if et == "tool_result"
        )
        last_call_idx = max(i for i, (et, _) in enumerate(parsed) if et == "tool_call")
        assert last_call_idx < first_result_idx

        # Sequence numbers strictly monotonic.
        seqs = [d["sequence"] for _, d in parsed]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    @pytest.mark.asyncio
    async def test_streaming_full_messages_submission_order(self) -> None:
        """Tool-role messages must reach the next LLM call in submission order."""

        @Tool(name="slow", description="slow")
        async def slow() -> ToolResult[str]:
            await asyncio.sleep(0.2)
            return ToolResult.ok("slow")

        @Tool(name="fast", description="fast")
        async def fast() -> ToolResult[str]:
            await asyncio.sleep(0.05)
            return ToolResult.ok("fast")

        @Tool(name="mid", description="mid")
        async def mid() -> ToolResult[str]:
            await asyncio.sleep(0.1)
            return ToolResult.ok("mid")

        tool_calls = [
            ToolCall(id=ToolCallId("id_slow"), name=ToolName("slow"), arguments={}),
            ToolCall(id=ToolCallId("id_fast"), name=ToolName("fast"), arguments={}),
            ToolCall(id=ToolCallId("id_mid"), name=ToolName("mid"), arguments={}),
        ]

        call_count = 0
        captured_messages: list[Message] = []

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=tool_calls, finish_reason="tool_calls")
            else:
                # Snapshot the messages passed to the second LLM call
                captured_messages.extend(kwargs["messages"])  # type: ignore[arg-type]
                yield StreamChunk(content="done")
                yield StreamChunk(finish_reason="stop")

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.stream = mock_stream

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                30.0,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[slow, fast, mid],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            async for _ in stream:
                pass

        tool_msgs = [m for m in captured_messages if m.role == Role.TOOL]
        assert [m.tool_call_id for m in tool_msgs] == ["id_slow", "id_fast", "id_mid"]

    @pytest.mark.asyncio
    async def test_streaming_mixed_success_and_failure(self) -> None:
        """A tool that raises must not break the batch; other results still emit."""

        @Tool(name="ok1", description="ok")
        async def ok1() -> ToolResult[str]:
            await asyncio.sleep(0.02)
            return ToolResult.ok("ok1")

        @Tool(name="bad", description="bad")
        async def bad() -> ToolResult[str]:
            raise ValueError("boom")

        @Tool(name="ok2", description="ok")
        async def ok2() -> ToolResult[str]:
            await asyncio.sleep(0.01)
            return ToolResult.ok("ok2")

        tool_calls = [
            ToolCall(id=ToolCallId("id_ok1"), name=ToolName("ok1"), arguments={}),
            ToolCall(id=ToolCallId("id_bad"), name=ToolName("bad"), arguments={}),
            ToolCall(id=ToolCallId("id_ok2"), name=ToolName("ok2"), arguments={}),
        ]

        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=tool_calls, finish_reason="tool_calls")
            else:
                yield StreamChunk(content="d")
                yield StreamChunk(finish_reason="stop")

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.stream = mock_stream

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                30.0,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[ok1, bad, ok2],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            parsed = [_parse_sse(s) async for s in stream]

        results_by_id = {
            d["tool_call_id"]: d for et, d in parsed if et == "tool_result"
        }
        assert results_by_id["id_ok1"]["success"] is True
        assert results_by_id["id_ok2"]["success"] is True
        assert results_by_id["id_bad"]["success"] is False
        assert "boom" in results_by_id["id_bad"]["error"]

    @pytest.mark.asyncio
    async def test_streaming_heartbeat_interleaving(self) -> None:
        """Slow tool emits heartbeats while a fast tool's result already arrived."""

        @Tool(name="slow", description="slow")
        async def slow() -> ToolResult[str]:
            await asyncio.sleep(0.3)
            return ToolResult.ok("slow")

        @Tool(name="fast", description="fast")
        async def fast() -> ToolResult[str]:
            await asyncio.sleep(0.01)
            return ToolResult.ok("fast")

        tool_calls = [
            ToolCall(id=ToolCallId("id_slow"), name=ToolName("slow"), arguments={}),
            ToolCall(id=ToolCallId("id_fast"), name=ToolName("fast"), arguments={}),
        ]

        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=tool_calls, finish_reason="tool_calls")
            else:
                yield StreamChunk(content="d")
                yield StreamChunk(finish_reason="stop")

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.stream = mock_stream

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                0.05,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[slow, fast],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            parsed = [_parse_sse(s) async for s in stream]

        heartbeats = [(et, d) for et, d in parsed if et == "heartbeat"]
        assert len(heartbeats) >= 1
        # All heartbeats carry the slow tool's id (fast tool finished before any HB).
        assert all(d["tool_call_id"] == "id_slow" for _, d in heartbeats)

        # Fast tool_result must appear before slow tool_result.
        results = [
            (i, d["tool_call_id"])
            for i, (et, d) in enumerate(parsed)
            if et == "tool_result"
        ]
        fast_idx = next(i for i, tid in results if tid == "id_fast")
        slow_idx = next(i for i, tid in results if tid == "id_slow")
        assert fast_idx < slow_idx

    @pytest.mark.asyncio
    async def test_parallel_path_with_single_tool(self) -> None:
        """N=1 still works after refactor — submission order trivially preserved."""

        @Tool(name="solo", description="solo")
        async def solo() -> ToolResult[str]:
            return ToolResult.ok("solo")

        tool_calls = [
            ToolCall(id=ToolCallId("only"), name=ToolName("solo"), arguments={}),
        ]
        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(tool_calls=tool_calls, finish_reason="tool_calls")
            else:
                yield StreamChunk(content="d")
                yield StreamChunk(finish_reason="stop")

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.stream = mock_stream

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[solo],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            parsed = [_parse_sse(s) async for s in stream]

        kinds = [et for et, _ in parsed]
        assert kinds.count("tool_call") == 1
        assert kinds.count("tool_result") == 1
        result = next(d for et, d in parsed if et == "tool_result")
        assert result["tool_call_id"] == "only"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_streaming_cancellation_no_orphan_tasks(self) -> None:
        """When the SSE consumer disconnects mid-batch, tool tasks must not orphan."""

        task_refs: list[weakref.ref[asyncio.Task[Any]]] = []
        original_create_task = asyncio.create_task

        def tracking_create_task(coro: Any, **kw: Any) -> asyncio.Task[Any]:
            t = original_create_task(coro, **kw)
            # Track only _run_tool_stream's task spawns.
            if "_run_tool_stream" in repr(coro):
                task_refs.append(weakref.ref(t))
            return t

        @Tool(name="hang", description="hang")
        async def hang() -> ToolResult[str]:
            await asyncio.sleep(10.0)
            return ToolResult.ok("never")

        tool_calls = [
            ToolCall(id=ToolCallId(f"id{i}"), name=ToolName("hang"), arguments={})
            for i in range(3)
        ]

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(tool_calls=tool_calls, finish_reason="tool_calls")

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.stream = mock_stream

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.asyncio.create_task",
                side_effect=tracking_create_task,
            ),
            patch(
                "neosian._foundation.agent.base.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                30.0,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[hang],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )

            # Pull tool_call events for all 3 tools, then close mid-batch.
            collected: list[str] = []
            async for sse in stream:
                collected.append(sse)
                if sum(1 for s in collected if "tool_call\n" in s) >= 3:
                    break
            await stream.aclose()  # type: ignore[attr-defined]

            # Let the event loop reap cancellations.
            await asyncio.sleep(0.05)

        live = [ref() for ref in task_refs]
        # All tracked tool wrapper tasks should be done (cancelled or completed).
        assert all(
            t is None or t.done() for t in live
        ), f"orphans: {[t for t in live if t and not t.done()]}"

    @pytest.mark.asyncio
    async def test_max_parallel_tools_caps_concurrency(self) -> None:
        """With max_parallel_tools=2, peak in-flight tools must not exceed 2."""

        in_flight = 0
        peak = 0
        lock = asyncio.Lock()

        async def _tracked() -> ToolResult[str]:
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.1)
                return ToolResult.ok("done")
            finally:
                async with lock:
                    in_flight -= 1

        @Tool(name="t1", description="t")
        async def t1() -> ToolResult[str]:
            return await _tracked()

        @Tool(name="t2", description="t")
        async def t2() -> ToolResult[str]:
            return await _tracked()

        @Tool(name="t3", description="t")
        async def t3() -> ToolResult[str]:
            return await _tracked()

        @Tool(name="t4", description="t")
        async def t4() -> ToolResult[str]:
            return await _tracked()

        mock_client = AsyncMock(spec=BaseLLMClient)
        tool_calls = [
            ToolCall(id=ToolCallId(f"id{i}"), name=ToolName(f"t{i+1}"), arguments={})
            for i in range(4)
        ]
        mock_client.complete.side_effect = [
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=tool_calls),
                usage=Usage(input_tokens=10, output_tokens=5),
                model="test-model",
            ),
            CompletionResponse(
                message=Message(role=Role.ASSISTANT, content="done"),
                usage=Usage(input_tokens=10, output_tokens=5),
                model="test-model",
            ),
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[t1, t2, t3, t4],
                enable_todo=False,
                max_parallel_tools=2,
            )
            agent = Agent(config=config)

            start = time.monotonic()
            await agent.run([Message(role=Role.USER, content="go")], stream=False)
            elapsed = time.monotonic() - start

        assert peak <= 2, f"expected peak<=2, got {peak}"
        # Two batches of 2 × 100ms ≈ 0.2s; full parallel would be ~0.1s.
        assert 0.18 < elapsed < 0.5, f"unexpected wall-clock {elapsed:.3f}s"

    def test_max_parallel_tools_validation_zero(self) -> None:
        """max_parallel_tools=0 must raise."""
        with pytest.raises(UnsupportedParameterError, match="max_parallel_tools"):
            AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[],
                enable_todo=False,
                max_parallel_tools=0,
            )

    def test_max_parallel_tools_validation_negative(self) -> None:
        """Negative max_parallel_tools must raise."""
        with pytest.raises(UnsupportedParameterError, match="max_parallel_tools"):
            AgentConfig(
                system_prompt=SystemPrompt("S"),
                tools=[],
                enable_todo=False,
                max_parallel_tools=-1,
            )


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
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            response = await agent.run(
                [Message(role=Role.USER, content="Hi")], stream=False
            )

            assert response.stop_reason == "max_tokens"


@pytest.mark.unit
class TestAgentCacheConversation:
    """cache_conversation must reach the client call."""

    @pytest.mark.asyncio
    async def test_cache_conversation_false_passed_to_client(self) -> None:
        """AgentConfig(cache_conversation=False) reaches client.complete."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Done."),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You transcribe PDFs."),
                tools=[],
                enable_todo=False,
                cache_conversation=False,
            )
            agent = Agent(config=config)

            await agent.run([Message(role=Role.USER, content="Hi")], stream=False)

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["cache_conversation"] is False

    @pytest.mark.asyncio
    async def test_cache_conversation_defaults_to_true(self) -> None:
        """Default config keeps conversation caching on."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Done."),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            await agent.run([Message(role=Role.USER, content="Hi")], stream=False)

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["cache_conversation"] is True


@pytest.mark.unit
class TestCapabilityAwareFallback:
    """Media-bearing conversations never downgrade to a non-supporting model."""

    def _doc_messages(self) -> list[Message]:
        from neosian._foundation.llm.base import DocumentBlock, TextBlock

        return [
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                    TextBlock(text="Transcribe this."),
                ],
            ),
        ]

    @pytest.mark.asyncio
    async def test_fallback_skipped_when_model_lacks_support(self) -> None:
        """Transient main failure + doc message + Groq fallback -> no fallback try."""
        from neosian._foundation.shared.exceptions import ModelFailedError

        main_client = AsyncMock(spec=BaseLLMClient)
        main_client.complete.side_effect = RuntimeError("rate limited")
        fallback_client = AsyncMock(spec=BaseLLMClient)

        clients = {
            Provider.ANTHROPIC: main_client,
            Provider.GROQ: fallback_client,
        }
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client.side_effect = lambda provider: clients[provider]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You transcribe PDFs."),
                tools=[],
                enable_todo=False,
                model=Model.CLAUDE_SONNET_5,
                fallback=FallbackConfig(model=Model.GROQ_LLAMA_3_3_70B),
            )
            agent = Agent(config=config)

            with pytest.raises(ModelFailedError):
                await agent.run(self._doc_messages(), stream=False)

            fallback_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_content_error_bypasses_wrapping_without_fallback(
        self,
    ) -> None:
        """No fallback configured: UnsupportedContentError re-raises unwrapped."""
        from neosian._foundation.shared.exceptions import UnsupportedContentError

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.side_effect = UnsupportedContentError(
            "Provider 'openai' does not support multimodal content blocks."
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                model=Model.GPT_5_NANO,
            )
            agent = Agent(config=config)

            with pytest.raises(UnsupportedContentError):
                await agent.run(self._doc_messages(), stream=False)

    @pytest.mark.asyncio
    async def test_fallback_to_supporting_model_still_works(self) -> None:
        """Main can't handle the doc, but a Claude fallback picks it up."""
        from neosian._foundation.shared.exceptions import UnsupportedContentError

        main_client = AsyncMock(spec=BaseLLMClient)
        main_client.complete.side_effect = UnsupportedContentError(
            "Provider 'openai' does not support multimodal content blocks."
        )
        fallback_client = AsyncMock(spec=BaseLLMClient)
        fallback_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="# Transcription"),
            usage=Usage(input_tokens=100, output_tokens=50),
            model="claude-sonnet-5",
            stop_reason="end_turn",
        )

        clients = {
            Provider.OPENAI: main_client,
            Provider.ANTHROPIC: fallback_client,
        }
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client.side_effect = lambda provider: clients[provider]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You transcribe PDFs."),
                tools=[],
                enable_todo=False,
                model=Model.GPT_5_NANO,
                fallback=FallbackConfig(model=Model.CLAUDE_SONNET_5),
            )
            agent = Agent(config=config)

            response = await agent.run(self._doc_messages(), stream=False)

            assert response.message.content == "# Transcription"
            assert response.stop_reason == "end_turn"
            fallback_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_sticky_fallback_routes_media_to_main(self) -> None:
        """Sticky-on-Groq session + doc message -> straight to the Claude main."""
        from neosian._foundation.shared.types import FallbackState

        main_client = AsyncMock(spec=BaseLLMClient)
        main_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="# Transcription"),
            usage=Usage(input_tokens=100, output_tokens=50),
            model="claude-sonnet-5",
        )
        fallback_client = AsyncMock(spec=BaseLLMClient)

        clients = {
            Provider.ANTHROPIC: main_client,
            Provider.GROQ: fallback_client,
        }
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client.side_effect = lambda provider: clients[provider]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You transcribe PDFs."),
                tools=[],
                enable_todo=False,
                model=Model.CLAUDE_SONNET_5,
                fallback=FallbackConfig(model=Model.GROQ_LLAMA_3_3_70B),
            )
            agent = Agent(config=config)

            fallback_state = FallbackState(
                using_fallback=True, successful_fallback_calls=1
            )
            full_messages = [
                Message(role=Role.SYSTEM, content="You transcribe PDFs."),
                *self._doc_messages(),
            ]

            response = await agent._execute_with_fallback_model(
                full_messages, fallback_state
            )

            assert response.message.content == "# Transcription"
            fallback_client.complete.assert_not_called()
            # Main handled it - sticky state resets
            assert fallback_state.using_fallback is False
