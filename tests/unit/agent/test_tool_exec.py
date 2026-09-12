"""Tool execution: heartbeats and the parallel batch."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.events import ToolProgressEvent
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    StreamChunk,
    ToolCall,
    Usage,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult
from tests.unit.agent.mocks import create_mock_router


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
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[fast_tool],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Run fast tool")]
            result = await agent.run(messages, stream=True)

            events = [event async for event in result]

            # Should have: ready, tool_call, tool_result, content, done —
            # and no tool_progress
            progress = [e for e in events if isinstance(e, ToolProgressEvent)]
            assert len(progress) == 0

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
                return_value=create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                0.05,
            ),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[slow_tool],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Run slow tool")]
            result = await agent.run(messages, stream=True)

            events = [event async for event in result]

            # Should have tool_progress events
            progress = [e for e in events if isinstance(e, ToolProgressEvent)]
            assert len(progress) >= 1

            # Verify progress carries the tool_call_id and integer elapsed_ms
            assert progress[0].tool_call_id == "call_slow"
            assert isinstance(progress[0].elapsed_ms, int)
            assert progress[0].elapsed_ms > 0

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
                return_value=create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                0.03,
            ),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[delayed_tool],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Run delayed tool")]
            result = await agent.run(messages, stream=True)

            events = [event async for event in result]

            progress = [e for e in events if isinstance(e, ToolProgressEvent)]
            assert len(progress) >= 1

            # Verify the tool_call_id rides the progress event
            assert progress[0].tool_call_id == "unique_id_123"


@pytest.mark.unit
class TestAgentParallelToolExecution:
    """Test parallel dispatch of tool calls from a single assistant turn."""

    @pytest.mark.asyncio
    async def test_blocking_tools_run_concurrently(self) -> None:
        """Three tools of a batch are all in flight at once — parallel, not
        one after another (pinned by a counter, never by the wall clock)."""
        in_flight = 0
        peak = 0

        async def _overlap(delay: float, result: str) -> ToolResult[str]:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(delay)
                return ToolResult.ok(result)
            finally:
                in_flight -= 1

        @Tool(name="t_a", description="A")
        async def t_a() -> ToolResult[str]:
            return await _overlap(0.06, "a")

        @Tool(name="t_b", description="B")
        async def t_b() -> ToolResult[str]:
            return await _overlap(0.02, "b")

        @Tool(name="t_c", description="C")
        async def t_c() -> ToolResult[str]:
            return await _overlap(0.04, "c")

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
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="S",
                tools=[t_a, t_b, t_c],
                enable_todo=False,
            )
            agent = Agent(config=config)
            messages = [Message(role=Role.USER, content="go")]

            response = await agent.run(messages, stream=False)

            assert peak == 3, f"expected all three tools in flight, peak {peak}"
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
                return_value=create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                30.0,
            ),
        ):
            config = AgentConfig(
                system_prompt="S",
                tools=[slow, fast, mid],
                enable_todo=False,
            )
            agent = Agent(config=config)
            messages = [Message(role=Role.USER, content="go")]
            stream = await agent.run(messages, stream=True)
            parsed = [(e.type.value, e.to_dict()) async for e in stream]

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
                return_value=create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                30.0,
            ),
        ):
            config = AgentConfig(
                system_prompt="S",
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
                return_value=create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                30.0,
            ),
        ):
            config = AgentConfig(
                system_prompt="S",
                tools=[ok1, bad, ok2],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            parsed = [(e.type.value, e.to_dict()) async for e in stream]

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
                return_value=create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS",
                0.05,
            ),
        ):
            config = AgentConfig(
                system_prompt="S",
                tools=[slow, fast],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            parsed = [(e.type.value, e.to_dict()) async for e in stream]

        heartbeats = [(et, d) for et, d in parsed if et == "tool_progress"]
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
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="S",
                tools=[solo],
                enable_todo=False,
            )
            agent = Agent(config=config)
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            parsed = [(e.type.value, e.to_dict()) async for e in stream]

        kinds = [et for et, _ in parsed]
        assert kinds.count("tool_call") == 1
        assert kinds.count("tool_result") == 1
        result = next(d for et, d in parsed if et == "tool_result")
        assert result["tool_call_id"] == "only"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_streaming_cancellation_no_orphan_tasks(self) -> None:
        """When the SSE consumer disconnects mid-batch, the running tools
        are cancelled — the tool body observes it — and nothing outlives
        the stream (AG-1)."""
        observed: list[str] = []

        @Tool(name="hang", description="hang")
        async def hang() -> ToolResult[str]:
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                observed.append("cancelled")
                raise
            return ToolResult.ok("never")

        tool_calls = tuple(
            ToolCall(id=ToolCallId(f"id{i}"), name=ToolName("hang"), arguments={})
            for i in range(3)
        )
        fake = FakeClient(FakeScript(turns=(FakeTurn(tool_calls=tool_calls),)))

        with patch(
            "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS",
            0.01,
        ):
            agent = Agent(
                config=AgentConfig(
                    system_prompt="S",
                    tools=[hang],
                    model=Model.FAKE,
                    enable_todo=False,
                    client_factory=lambda _: fake,
                )
            )
            before = asyncio.all_tasks()
            stream = await agent.run(
                [Message(role=Role.USER, content="go")], stream=True
            )
            assert isinstance(stream, AsyncGenerator)

            # The first progress event means every tool is running (the
            # batch is spawned whole); close mid-batch there.
            async for event in stream:
                if isinstance(event, ToolProgressEvent):
                    break
            await stream.aclose()

        assert observed == ["cancelled"] * 3
        assert not (asyncio.all_tasks() - before)

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
                await asyncio.sleep(0.05)
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
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="S",
                tools=[t1, t2, t3, t4],
                enable_todo=False,
                max_parallel_tools=2,
            )
            agent = Agent(config=config)

            await agent.run([Message(role=Role.USER, content="go")], stream=False)

        # The cap is reached (two enter and park before a third can) and
        # never exceeded — both pinned deterministically, no wall clock.
        assert peak == 2, f"expected peak == 2, got {peak}"

    def test_max_parallel_tools_validation_zero(self) -> None:
        """max_parallel_tools=0 must raise."""
        with pytest.raises(UnsupportedParameterError, match="max_parallel_tools"):
            AgentConfig(
                system_prompt="S",
                tools=[],
                enable_todo=False,
                max_parallel_tools=0,
            )

    def test_max_parallel_tools_validation_negative(self) -> None:
        """Negative max_parallel_tools must raise."""
        with pytest.raises(UnsupportedParameterError, match="max_parallel_tools"):
            AgentConfig(
                system_prompt="S",
                tools=[],
                enable_todo=False,
                max_parallel_tools=-1,
            )
