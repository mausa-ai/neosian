"""The agent's response value object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from neosian._foundation.llm.base import (
    Message,
    ModelUsage,
    StopReason,
    ToolCall,
    Usage,
    normalize_stop_reason,
)
from neosian._foundation.shared.types import GuardrailResult
from neosian._foundation.tools.base import ToolResult


@dataclass(frozen=True, slots=True)
class AgentResponse:
    """Response from agent execution — one immutable value object.

    Rebuilds go through dataclasses.replace, never field-by-field
    (DESIGN §3 found-bug register #2).

    Attributes:
        message: The assistant's response message.
        tool_calls_made: List of tool calls made during execution.
        tool_results: Results from tool executions.
        usage: Token usage statistics.
        blocked: True if content was blocked by guardrails.
        guardrail_result: Detailed guardrail check results (if guardrails enabled).
        parsed: Parsed Pydantic model instance when response_format was provided.
            None when response_format was not used.
        stop_reason: Provider-native stop reason for the final completion —
            truncation is "max_tokens" on Anthropic and "length" on
            OpenAI-compatible providers. None on guardrail-blocked responses.
        model: Model string reported by the API for the final completion
            (fallback-aware — reflects the model that actually answered).
            None on guardrail-blocked responses.
        usage_by_model: Per-API-reported-model split of `usage`, one entry
            per distinct model in first-appearance order — a failed main
            attempt's billed tokens appear here beside the fallback's.
        turn_messages: The messages this run produced, in provider order;
            the last element is `message` by identity, and input +
            turn_messages replays as valid history (DESIGN §3). Empty on
            guardrail-blocked responses — a blocked turn is not replayable.
        iterations_exhausted: True when the tool loop hit `max_tool_iterations`
            and this is the toolless final call's answer (NF #170, AG-7).
    """

    message: Message
    tool_calls_made: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult[Any], ...] = ()
    usage: Usage = Usage(input_tokens=0, output_tokens=0)
    blocked: bool = False
    guardrail_result: GuardrailResult | None = None
    parsed: BaseModel | None = None
    stop_reason: str | None = None
    model: str | None = None
    usage_by_model: tuple[ModelUsage, ...] = ()
    turn_messages: tuple[Message, ...] = ()
    iterations_exhausted: bool = False

    @property
    def normalized_stop_reason(self) -> StopReason | None:
        """Provider-agnostic view of stop_reason."""
        return normalize_stop_reason(self.stop_reason)

    @property
    def truncated(self) -> bool:
        """True when the final completion was cut off at the output-token cap."""
        return self.normalized_stop_reason is StopReason.MAX_TOKENS
