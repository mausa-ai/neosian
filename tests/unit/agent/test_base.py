"""Tests for Agent core."""

from collections.abc import AsyncIterator
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
