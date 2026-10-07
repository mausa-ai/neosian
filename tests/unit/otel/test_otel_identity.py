"""The exporter carries run identity and true starts (N7, ledger #333)."""

import asyncio
from collections.abc import Mapping

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import AgentConfig, Model, ToolCallId, ToolName
from neosian._foundation.tools.base import Tool, ToolResult
from neosian.otel import otel_hooks

_USER = [Message(role=Role.USER, content="go")]


def _attrs(span: ReadableSpan) -> Mapping[str, object]:
    assert span.attributes is not None
    return span.attributes


@pytest.mark.unit
class TestIdentityOnSpans:
    async def test_every_span_of_a_run_carries_its_id_and_true_start(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        @Tool(name="quick", description="returns at once")
        async def quick() -> ToolResult[str]:
            return ToolResult.ok("q")

        @Tool(name="slow", description="sleeps")
        async def slow() -> ToolResult[str]:
            await asyncio.sleep(0.15)
            return ToolResult.ok("s")

        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(
                        tool_calls=(
                            ToolCall(
                                id=ToolCallId("c1"),
                                name=ToolName("quick"),
                                arguments={},
                            ),
                            ToolCall(
                                id=ToolCallId("c2"), name=ToolName("slow"), arguments={}
                            ),
                        )
                    ),
                    FakeTurn(content="done"),
                )
            )
        )
        agent = Agent(
            AgentConfig(
                system_prompt="You run tools.",
                model=Model.FAKE,
                enable_todo=False,
                tools=[quick, slow],
                hooks=otel_hooks(tracer_provider=provider),
                client_factory=lambda _: fake,
            )
        )
        await agent.run(_USER, stream=False)

        spans = exporter.get_finished_spans()
        assert len(spans) == 5  # two chats, two tools, one turn
        run_ids = {_attrs(s)["neosian.run_id"] for s in spans}
        assert len(run_ids) == 1
        assert all("neosian.parent_run_id" not in _attrs(s) for s in spans)
        assert all(
            _attrs(s)["neosian.purpose"] == "agent"
            for s in spans
            if s.name.startswith("chat")
        )

        by_name = {s.name: s for s in spans}
        quick_span, slow_span = (
            by_name["execute_tool quick"],
            by_name["execute_tool slow"],
        )
        assert quick_span.start_time is not None and slow_span.start_time is not None
        # Both tools started together; the quick one is drawn at the
        # start of the batch, not at its end.
        assert abs(quick_span.start_time - slow_span.start_time) < 50_000_000
        assert slow_span.end_time is not None
        assert slow_span.end_time - slow_span.start_time >= 100_000_000
