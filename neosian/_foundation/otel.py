"""OpenTelemetry span export from the agent hooks (DESIGN §3).

`otel_hooks()` returns an `AgentHooks` whose four callbacks emit one
span per observed event. Hooks fire after the work they observe has
completed and carry its wall time, so spans are recorded post hoc:
end = the hook's clock now, start = end - the event's `duration_ms`.
The spans are flat and independent — the hook events carry no run
identity, and fabricating parent links across them would misattribute;
a fallback switch is a point-in-time span. Attribute names follow the
OpenTelemetry GenAI semantic conventions where one exists and the
`neosian.*` namespace elsewhere. Spans carry names, models, token
counts, and outcomes — never message content, tool arguments, or tool
results (telemetry must not become a second store of user data).

The `opentelemetry` import is function-local on first use (the mcp
sdk.py idiom): `import neosian` and `import neosian.otel` stay
dependency-free without the `otel` extra, pinned by subprocess tests.
"""

from __future__ import annotations

import importlib.metadata
import time
from typing import TYPE_CHECKING, Any

from neosian._foundation.agent.hooks import (
    AgentHooks,
    FallbackEvent,
    LlmCallEvent,
    ToolEvent,
    TurnEvent,
)

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer, TracerProvider
    from opentelemetry.util.types import AttributeValue

_INSTALL_HINT = (
    "The neosian OTel exporter requires the 'otel' extra — "
    "uv add 'neosian[otel]' (or pip install 'neosian[otel]')"
)

_NS_PER_MS = 1_000_000


def otel_hooks(*, tracer_provider: TracerProvider | None = None) -> AgentHooks:
    """Build hooks that record one OTel span per agent event.

    Args:
        tracer_provider: The provider to record through; the global one
            when omitted. Passing it explicitly is the testable path —
            an SDK `TracerProvider` over an in-memory exporter sees the
            spans without touching global state.

    Returns:
        An `AgentHooks` ready for `AgentConfig(hooks=...)`, or to
        compose with caller hooks. Callbacks are synchronous and never
        raise into the run (hooks default to swallow-unless-strict).

    Raises:
        ImportError: When `opentelemetry-api` is not installed — the
            message names the `otel` extra.
    """
    trace = _load_trace()
    provider = (
        tracer_provider if tracer_provider is not None else trace.get_tracer_provider()
    )
    try:
        version: str | None = importlib.metadata.version("neosian")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        version = None
    tracer: Tracer = provider.get_tracer("neosian", version)
    error_code = trace.StatusCode.ERROR

    def _record(
        name: str,
        duration_ms: int,
        attributes: dict[str, AttributeValue],
        *,
        error: str | None = None,
    ) -> None:
        end = time.time_ns()
        start = end - duration_ms * _NS_PER_MS
        span = tracer.start_span(name, start_time=start, attributes=attributes)
        if error is not None:
            span.set_status(error_code, error)
        span.end(end_time=end)

    def on_llm_call(event: LlmCallEvent) -> None:
        attributes: dict[str, AttributeValue] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": event.provider.value,
            "gen_ai.request.model": event.requested_model,
            "neosian.iteration": event.iteration,
            "neosian.streamed": event.streamed,
        }
        if event.model is not None:
            attributes["gen_ai.response.model"] = event.model
        if event.stop_reason is not None:
            attributes["neosian.stop_reason"] = event.stop_reason
        if event.usage is not None:
            attributes["gen_ai.usage.input_tokens"] = event.usage.input_tokens
            attributes["gen_ai.usage.output_tokens"] = event.usage.output_tokens
            attributes["neosian.usage.cache_read_tokens"] = (
                event.usage.cache_read_tokens
            )
            attributes["neosian.usage.cache_write_tokens"] = (
                event.usage.cache_write_tokens
            )
        if event.error_code is not None:
            attributes["error.type"] = event.error_code
        _record(
            f"chat {event.requested_model}",
            event.duration_ms,
            attributes,
            error=event.error_code,
        )

    def on_tool(event: ToolEvent) -> None:
        attributes: dict[str, AttributeValue] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": str(event.name),
            "gen_ai.tool.call.id": str(event.call_id),
            "neosian.iteration": event.iteration,
            "neosian.tool.success": event.result.success,
        }
        _record(
            f"execute_tool {event.name}",
            event.duration_ms,
            attributes,
            error=None if event.result.success else "tool_error",
        )

    def on_turn(event: TurnEvent) -> None:
        response = event.response
        attributes: dict[str, AttributeValue] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.usage.input_tokens": response.usage.input_tokens,
            "gen_ai.usage.output_tokens": response.usage.output_tokens,
            "neosian.streamed": event.streamed,
            "neosian.blocked": response.blocked,
            "neosian.tool_calls": len(response.tool_calls_made),
        }
        if response.model is not None:
            attributes["gen_ai.response.model"] = response.model
        _record("invoke_agent", event.duration_ms, attributes)

    def on_fallback(event: FallbackEvent) -> None:
        attributes: dict[str, AttributeValue] = {
            "neosian.fallback.from_model": event.from_model,
            "neosian.fallback.to_model": event.to_model,
            "neosian.fallback.reason": event.reason,
            "neosian.fallback.sticky": event.sticky,
            "neosian.streamed": event.streamed,
        }
        if event.cause_code is not None:
            attributes["neosian.fallback.cause_code"] = event.cause_code
        if event.provider_status is not None:
            attributes["neosian.fallback.provider_status"] = event.provider_status
        _record("agent.fallback", 0, attributes)

    return AgentHooks(
        on_turn=on_turn,
        on_llm_call=on_llm_call,
        on_tool=on_tool,
        on_fallback=on_fallback,
    )


def _load_trace() -> Any:
    """Import the OTel trace API, raising a helpful ImportError without
    the extra."""
    try:
        from opentelemetry import trace
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc
    return trace
