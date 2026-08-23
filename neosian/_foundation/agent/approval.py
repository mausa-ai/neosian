"""The tool-approval gate (DESIGN §17).

`ToolGateConfig` is the interception seam: one approver callable with
complete authority over every tool call — builtins included — invoked
before the tool executes on both paths (the `execute_tool` leaf, so
blocking/streaming parity is by construction). An instant approve is no
pause; a denial is an in-band `ToolResult.fail` the model sees and
adapts to, riding the existing `tool_result` frame — the wire vocabulary
is untouched (ledger #106).

Default-deny is code, not configuration: a timeout, an approver
exception, or a non-`ToolDecision` return all deny, each naming its
cause. There is deliberately no FAIL_OPEN analogue (ledger #107).

The approver is sync-or-async like hooks. A sync approver blocks the
event loop and cannot be timed out mid-call; parallel tool batches
invoke the approver concurrently, capped by `max_parallel_tools`.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from neosian._foundation.shared.types import ToolCallId, ToolName
from neosian._foundation.tools.base import ToolResult

DEFAULT_APPROVAL_TIMEOUT_SECONDS: float = 60.0

_DENIED = "Tool '{tool_name}' denied by the approval gate"
_TIMED_OUT = (
    "Tool '{tool_name}' approval timed out after {timeout}s; denied (default-deny)"
)
_APPROVER_FAILED = "Tool '{tool_name}' approver failed: {error}; denied (default-deny)"
_APPROVER_INVALID = (
    "Tool '{tool_name}' approver returned {type_name}, "
    "not ToolDecision; denied (default-deny)"
)


@dataclass(frozen=True, slots=True)
class ToolApprovalRequest:
    """One tool call awaiting approval, built after the tool lookup
    succeeds — an unknown tool stays `TOOL_NOT_FOUND` and never reaches
    the approver."""

    call_id: ToolCallId
    name: ToolName
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolDecision:
    """The approver's verdict; `reason` reaches the model on a denial."""

    approved: bool
    reason: str | None = None


Approver = Callable[[ToolApprovalRequest], "ToolDecision | Awaitable[ToolDecision]"]


@dataclass(frozen=True, slots=True)
class ToolGateConfig:
    """The tool-approval gate: when set on `AgentConfig.tool_gate`, every
    tool call passes through `approver` before executing.

    `timeout_seconds` bounds an async approver's decision; `None` waits
    indefinitely (interactive hosts). No decision is a denial, always.
    """

    approver: Approver
    timeout_seconds: float | None = DEFAULT_APPROVAL_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not callable(self.approver):
            raise ValueError("ToolGateConfig.approver must be callable")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError(
                "ToolGateConfig.timeout_seconds must be positive or None, "
                f"got {self.timeout_seconds}"
            )


async def gate_tool_call(
    gate: ToolGateConfig, request: ToolApprovalRequest
) -> ToolResult[Any] | None:
    """Resolve one approval; None means approved, a failure means denied.

    Never raises: every non-approval collapses to a denial naming its
    cause. `except Exception` spares CancelledError — a consumer
    disconnect during the pause propagates.
    """
    try:
        outcome = gate.approver(request)
        if inspect.isawaitable(outcome):
            if gate.timeout_seconds is not None:
                decision: object = await asyncio.wait_for(
                    outcome, timeout=gate.timeout_seconds
                )
            else:
                decision = await outcome
        else:
            decision = outcome
    except TimeoutError:
        return ToolResult.fail(
            _TIMED_OUT.format(tool_name=request.name, timeout=gate.timeout_seconds)
        )
    except Exception as e:
        return ToolResult.fail(_APPROVER_FAILED.format(tool_name=request.name, error=e))

    if not isinstance(decision, ToolDecision):
        return ToolResult.fail(
            _APPROVER_INVALID.format(
                tool_name=request.name, type_name=type(decision).__name__
            )
        )
    if decision.approved:
        return None
    message = _DENIED.format(tool_name=request.name)
    if decision.reason:
        message = f"{message}: {decision.reason}"
    return ToolResult.fail(message)
