"""OTel span export from the agent hooks — keyless (DESIGN §3, ledger #83).

An SDK `TracerProvider` over an `InMemorySpanExporter` receives the
spans; the end-to-end tests drive a real FakeProvider agent — the NV
done-when ("the OTel extra emits spans keylessly").
"""

import sys
from collections.abc import Mapping

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from neosian import Agent, AgentConfig, FallbackConfig, Model
from neosian._foundation.agent.hooks import FallbackEvent, ToolEvent
from neosian._foundation.llm.base import Message, Role, ToolCall, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import (
    Provider,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult
from neosian.otel import otel_hooks

_USER = [Message(role=Role.USER, content="Hi")]


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def provider(exporter: InMemorySpanExporter) -> TracerProvider:
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    return tracer_provider


def _config(provider: TracerProvider, **overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "system_prompt": "You are a test agent.",
        "model": Model.FAKE,
        "enable_todo": False,
        "hooks": otel_hooks(tracer_provider=provider),
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


def _spans(exporter: InMemorySpanExporter) -> dict[str, list[ReadableSpan]]:
    grouped: dict[str, list[ReadableSpan]] = {}
    for span in exporter.get_finished_spans():
        grouped.setdefault(span.name, []).append(span)
    return grouped


def _attrs(span: ReadableSpan) -> Mapping[str, object]:
    assert span.attributes is not None
    return span.attributes


@pytest.mark.unit
class TestEndToEnd:
    async def test_tool_loop_emits_chat_tool_and_turn_spans(
        self, exporter: InMemorySpanExporter, provider: TracerProvider
    ) -> None:
        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(
                        tool_calls=(
                            ToolCall(
                                id=ToolCallId("c1"),
                                name=ToolName("greet"),
                                arguments={"name": "ada"},
                            ),
                        ),
                        usage=Usage(input_tokens=10, output_tokens=5),
                    ),
                    FakeTurn(
                        content="Greeted ada",
                        usage=Usage(input_tokens=30, output_tokens=4),
                    ),
                )
            )
        )
        agent = Agent(_config(provider, tools=[greet], client_factory=lambda _: fake))
        await agent.run(_USER, stream=False)

        spans = _spans(exporter)
        assert sorted(spans) == ["chat fake", "execute_tool greet", "invoke_agent"]
        assert len(spans["chat fake"]) == 2

        chat = _attrs(spans["chat fake"][0])
        assert chat["gen_ai.operation.name"] == "chat"
        assert chat["gen_ai.provider.name"] == Provider.FAKE.value
        assert chat["gen_ai.request.model"] == Model.FAKE.value
        assert chat["gen_ai.response.model"] == Model.FAKE.value
        assert chat["gen_ai.usage.input_tokens"] == 10
        assert chat["gen_ai.usage.output_tokens"] == 5
        assert chat["neosian.streamed"] is False
        span = spans["chat fake"][0]
        assert span.start_time is not None and span.end_time is not None
        assert span.start_time <= span.end_time

        tool = spans["execute_tool greet"][0]
        assert _attrs(tool)["gen_ai.tool.name"] == "greet"
        assert _attrs(tool)["gen_ai.tool.call.id"] == "c1"
        assert _attrs(tool)["neosian.tool.success"] is True
        assert tool.status.status_code is StatusCode.UNSET

        turn = _attrs(spans["invoke_agent"][0])
        assert turn["gen_ai.usage.input_tokens"] == 40
        assert turn["gen_ai.usage.output_tokens"] == 9
        assert turn["neosian.tool_calls"] == 1
        assert turn["neosian.blocked"] is False

    async def test_streaming_run_marks_spans_streamed(
        self, exporter: InMemorySpanExporter, provider: TracerProvider
    ) -> None:
        fake = FakeClient(FakeScript(turns=(FakeTurn(content="ok"),)))
        agent = Agent(_config(provider, client_factory=lambda _: fake))
        async for _event in await agent.run(_USER, stream=True):
            pass

        spans = _spans(exporter)
        assert _attrs(spans["invoke_agent"][0])["neosian.streamed"] is True
        assert _attrs(spans["chat fake"][0])["neosian.streamed"] is True

    async def test_fallback_emits_a_point_span_and_an_error_chat(
        self, exporter: InMemorySpanExporter, provider: TracerProvider
    ) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main model down")),
                    FakeTurn(content="recovered"),
                )
            )
        )
        agent = Agent(
            _config(
                provider,
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        await agent.run(_USER, stream=False)

        spans = _spans(exporter)
        fallback = spans["agent.fallback"][0]
        assert _attrs(fallback)["neosian.fallback.from_model"] == Model.FAKE.value
        assert _attrs(fallback)["neosian.fallback.to_model"] == Model.FAKE_SMALL.value
        assert _attrs(fallback)["neosian.fallback.sticky"] is False
        assert fallback.start_time == fallback.end_time

        failed = spans[f"chat {Model.FAKE.value}"][0]
        assert failed.status.status_code is StatusCode.ERROR
        assert _attrs(failed)["error.type"] == failed.status.description
        recovered = spans[f"chat {Model.FAKE_SMALL.value}"][0]
        assert recovered.status.status_code is StatusCode.UNSET


@pytest.mark.unit
class TestCallbacks:
    def test_failed_tool_marks_the_span_error(
        self, exporter: InMemorySpanExporter, provider: TracerProvider
    ) -> None:
        hooks = otel_hooks(tracer_provider=provider)
        assert hooks.on_tool is not None
        hooks.on_tool(
            ToolEvent(
                call_id=ToolCallId("c9"),
                name=ToolName("boom"),
                arguments={},
                result=ToolResult.fail("exploded"),
                duration_ms=250,
                iteration=1,
            )
        )
        (span,) = exporter.get_finished_spans()
        assert span.name == "execute_tool boom"
        assert span.status.status_code is StatusCode.ERROR
        assert _attrs(span)["neosian.tool.success"] is False
        assert span.start_time is not None and span.end_time is not None
        assert span.end_time - span.start_time == 250 * 1_000_000

    def test_sticky_fallback_span_carries_no_cause(
        self, exporter: InMemorySpanExporter, provider: TracerProvider
    ) -> None:
        hooks = otel_hooks(tracer_provider=provider)
        assert hooks.on_fallback is not None
        hooks.on_fallback(
            FallbackEvent(
                from_model="fake-small",
                to_model="fake",
                reason="retry main",
                cause_code=None,
                provider_status=None,
                sticky=True,
                streamed=False,
            )
        )
        (span,) = exporter.get_finished_spans()
        assert _attrs(span)["neosian.fallback.sticky"] is True
        assert "neosian.fallback.cause_code" not in _attrs(span)


@pytest.mark.unit
class TestLazyImport:
    def test_missing_api_names_the_reinstall(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        with pytest.raises(ImportError, match="uv add neosian"):
            otel_hooks()
