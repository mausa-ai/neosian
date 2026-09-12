"""The streaming run: events, usage reporting, and the provider stream closed with the consumer."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.events import (
    BlockedEvent,
    ContentEvent,
    DoneEvent,
    ReadyEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from neosian._foundation.agent.guards import check_guard_and_block
from neosian._foundation.agent.hooks import AgentHooks, TurnEvent
from neosian._foundation.agent.tool_scope import ToolScope
from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    StreamChunk,
    ToolCall,
    Usage,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import (
    AgentConfig,
    FallbackConfig,
    Model,
    Provider,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult
from tests.unit.agent.mocks import create_mock_router


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
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            result = await agent.run(messages, stream=True)

            events = [event async for event in result]

            # Ready first, then content deltas, then done
            assert len(events) == 4
            assert isinstance(events[0], ReadyEvent)
            assert isinstance(events[1], ContentEvent)
            assert events[1].content == "Hello "
            assert isinstance(events[2], ContentEvent)
            assert events[2].content == "world!"
            assert isinstance(events[3], DoneEvent)
            assert [e.sequence for e in events] == [1, 2, 3, 4]

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
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are a calculator.",
                tools=[add],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="What is 2 + 3?")]
            result = await agent.run(messages, stream=True)

            events = [event async for event in result]

            # Should have: ready, tool_call, tool_result, content, done
            assert len(events) == 5
            assert isinstance(events[0], ReadyEvent)

            assert isinstance(events[1], ToolCallEvent)
            assert events[1].name == "add"

            assert isinstance(events[2], ToolResultEvent)
            assert events[2].success is True

            assert isinstance(events[3], ContentEvent)
            assert events[3].content == "The sum is 5."

            assert isinstance(events[4], DoneEvent)

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
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            result = await agent.run(messages, stream=True)

            # Result should be an async iterator
            assert hasattr(result, "__anext__")


@pytest.mark.unit
class TestStreamingUsageReporting:
    """Test usage accumulation and reporting in the streaming path."""

    @pytest.mark.asyncio
    async def test_streaming_sums_usage_across_iterations(self) -> None:
        """The done frame should carry usage summed over all tool iterations."""

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("add"),
            arguments={"a": 2, "b": 3},
        )

        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(
                    tool_calls=[tool_call],
                    finish_reason="tool_calls",
                    usage=Usage(input_tokens=20, output_tokens=10),
                )
            else:
                yield StreamChunk(content="The sum is 5.")
                yield StreamChunk(
                    finish_reason="stop",
                    usage=Usage(input_tokens=30, output_tokens=15),
                )

        mock_client.stream = mock_stream

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
            result = await agent.run(messages, stream=True)

            events = [event async for event in result]

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done_events) == 1
        usage = done_events[0].usage
        # 20+10 (tool iteration) + 30+15 (final iteration) = 75
        assert usage is not None
        assert usage.input_tokens == 50
        assert usage.output_tokens == 25
        assert usage.total_tokens == 75

    @pytest.mark.asyncio
    async def test_final_call_without_finish_reason_still_terminates(self) -> None:
        """A final-call stream that ends without a finish_reason (and no
        usage chunk) still yields its terminal and fires on_turn exactly
        once (AG-4) — the tool loop's terminal was already unconditional."""

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        mock_client = AsyncMock(spec=BaseLLMClient)
        tool_call = ToolCall(
            id=ToolCallId("call_1"), name=ToolName("add"), arguments={"a": 2, "b": 3}
        )
        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(
                    tool_calls=[tool_call],
                    finish_reason="tool_calls",
                    usage=Usage(input_tokens=20, output_tokens=10),
                )
            else:
                yield StreamChunk(content="Best guess: 5.")

        mock_client.stream = mock_stream
        turns: list[TurnEvent] = []

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are a calculator.",
                tools=[add],
                enable_todo=False,
                hooks=AgentHooks(on_turn=turns.append),
                max_tool_iterations=1,
            )
            agent = Agent(config=config)
            result = await agent.run(
                [Message(role=Role.USER, content="What is 2 + 3?")], stream=True
            )
            events = [event async for event in result]

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done_events) == 1
        assert done_events[0].stop_reason is None
        assert done_events[0].raw_stop_reason is None
        assert done_events[0].usage is not None
        assert done_events[0].usage.total_tokens == 30
        assert len(turns) == 1
        assert turns[0].response.message.content == "Best guess: 5."

    @pytest.mark.asyncio
    async def test_streaming_max_iterations_includes_prior_usage(self) -> None:
        """The forced final response should merge usage from exhausted iterations."""

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("add"),
            arguments={"a": 2, "b": 3},
        )

        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(
                    tool_calls=[tool_call],
                    finish_reason="tool_calls",
                    usage=Usage(input_tokens=20, output_tokens=10),
                )
            else:
                # Forced final response (tools=None after max iterations)
                yield StreamChunk(content="Best guess: 5.")
                yield StreamChunk(
                    finish_reason="stop",
                    usage=Usage(input_tokens=30, output_tokens=15),
                )

        mock_client.stream = mock_stream

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are a calculator.",
                tools=[add],
                enable_todo=False,
                max_tool_iterations=1,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="What is 2 + 3?")]
            result = await agent.run(messages, stream=True)

            events = [event async for event in result]

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done_events) == 1
        assert done_events[0].usage is not None
        assert done_events[0].usage.total_tokens == 75

    @pytest.mark.asyncio
    async def test_model_failed_error_carries_usage(self) -> None:
        """ModelFailedError should carry accumulated + partial usage."""
        from neosian._foundation.shared.exceptions import ModelFailedError

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        mock_client = AsyncMock(spec=BaseLLMClient)

        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("add"),
            arguments={"a": 2, "b": 3},
        )

        call_count = 0

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(
                    tool_calls=[tool_call],
                    finish_reason="tool_calls",
                    usage=Usage(input_tokens=20, output_tokens=10),
                )
            else:
                # Partial usage arrives (input tokens known), then the stream dies
                yield StreamChunk(usage=Usage(input_tokens=30, output_tokens=0))
                raise RuntimeError("connection dropped")

        mock_client.stream = mock_stream

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
            result = await agent.run(messages, stream=True)

            with pytest.raises(ModelFailedError) as exc_info:
                async for _ in result:
                    pass

        # 20+10 (completed iteration) + 30+0 (partial of failed turn) = 60
        assert exc_info.value.usage is not None
        assert exc_info.value.usage.total_tokens == 60

    @pytest.mark.asyncio
    async def test_fallback_exhausted_error_sums_both_attempts(self) -> None:
        """FallbackExhaustedError should sum usage billed by both attempts."""
        from neosian._foundation.shared.exceptions import FallbackExhaustedError

        async def main_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(usage=Usage(input_tokens=20, output_tokens=0))
            raise RuntimeError("main died")

        async def fallback_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(usage=Usage(input_tokens=5, output_tokens=0))
            raise RuntimeError("fallback died")

        main_client = AsyncMock(spec=BaseLLMClient)
        main_client.stream = main_stream
        fallback_client = AsyncMock(spec=BaseLLMClient)
        fallback_client.stream = fallback_stream

        clients = {
            Provider.ANTHROPIC: main_client,
            Provider.CEREBRAS: fallback_client,
        }
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client_for.side_effect = lambda model: clients[
            model.provider
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
                model=Model.CLAUDE_SONNET_5,
                fallback=FallbackConfig(model=Model.CEREBRAS_QWEN_3_8_27B),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            result = await agent.run(messages, stream=True)

            with pytest.raises(FallbackExhaustedError) as exc_info:
                async for _ in result:
                    pass

        # 20 (main attempt) + 5 (fallback attempt) — both were billed
        assert exc_info.value.usage is not None
        assert exc_info.value.usage.input_tokens == 25

    @pytest.mark.asyncio
    async def test_check_guard_and_block_includes_usage(self) -> None:
        """The BlockedEvent should carry the usage passed in."""
        from neosian._foundation.shared.types import GuardrailsConfig, PolicyResult

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            agent._guardrails = GuardrailsConfig(block_on_input=True)

        async def guard() -> tuple[bool, PolicyResult | None]:
            return (False, PolicyResult(safe=False, rationale="policy violation"))

        guard_task = asyncio.ensure_future(guard())
        await guard_task

        blocked = await check_guard_and_block(
            agent._run_context(None, ToolScope()),
            guard_task,
            usage=Usage(input_tokens=20, output_tokens=10),
        )

        assert blocked is not None
        assert isinstance(blocked, BlockedEvent)
        assert blocked.rationale == "policy violation"
        assert blocked.usage is not None
        assert blocked.usage.total_tokens == 30


class _ClosingFake(FakeClient):
    """A fake that counts how many of its streams were closed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.closed_streams = 0

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        try:
            async for chunk in super().stream(*args, **kwargs):
                yield chunk
        finally:
            self.closed_streams += 1


@pytest.mark.unit
class TestProviderStreamClosure:
    """A consumer's aclose() closes the provider stream with it (AG-14) —
    synchronously, not whenever the garbage collector gets to it."""

    def _agent(self, fake: FakeClient, **kwargs: Any) -> Agent:
        return Agent(
            AgentConfig(
                system_prompt="S",
                model=Model.FAKE,
                enable_todo=False,
                client_factory=lambda _: fake,
                **kwargs,
            )
        )

    async def _close_at_first_content(self, agent: Agent) -> None:
        stream = await agent.run([Message(role=Role.USER, content="go")], stream=True)
        assert isinstance(stream, AsyncGenerator)
        async for event in stream:
            if isinstance(event, ContentEvent):
                break
        await stream.aclose()

    async def test_tool_loop_stream_closed_on_consumer_disconnect(self) -> None:
        fake = _ClosingFake(
            FakeScript(turns=(FakeTurn(content="a long answer"),), chunk_chars=2)
        )
        await self._close_at_first_content(self._agent(fake))
        assert fake.closed_streams == 1

    async def test_final_call_stream_closed_on_consumer_disconnect(self) -> None:
        @Tool(name="noop", description="noop")
        async def noop() -> ToolResult[str]:
            return ToolResult.ok("ok")

        call = ToolCall(id=ToolCallId("c1"), name=ToolName("noop"), arguments={})
        fake = _ClosingFake(
            FakeScript(
                turns=(FakeTurn(tool_calls=(call,)), FakeTurn(content="final words")),
                chunk_chars=2,
            )
        )
        agent = self._agent(fake, tools=[noop], max_tool_iterations=1)
        await self._close_at_first_content(agent)
        assert fake.closed_streams == 2
