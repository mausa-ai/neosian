"""Tool execution: the gated single call, heartbeats, and the
streaming fan-out worker."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from neosian._foundation.agent.approval import ToolApprovalRequest, gate_tool_call
from neosian._foundation.agent.events import (
    AgentEvent,
    MemoryWriteEvent,
    ToolProgressEvent,
    ToolResultEvent,
)
from neosian._foundation.agent.lifetimes import reap
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.shared.constants import ErrorMessages, Streaming
from neosian._foundation.shared.exceptions import ToolExecutionError
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import ToolCallId
from neosian._foundation.tools.base import ToolResult, get_tool_metadata
from neosian._foundation.tools.schema import rejection, validate_arguments

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent


async def execute_tool(agent: Agent, tool_call: ToolCall) -> ToolResult[Any]:
    """Execute a single tool call.

    Args:
        tool_call: The tool call to execute.

    Returns:
        ToolResult from the tool execution.
    """
    tool_func = agent._tools.get(tool_call.name)

    if tool_func is None:
        return ToolResult.fail(
            ErrorMessages.TOOL_NOT_FOUND.format(tool_name=tool_call.name)
        )

    # The approval gate (DESIGN §17) sits after the lookup — an unknown
    # tool never reaches the approver — and before execution on both
    # paths. On the streaming path this runs inside the heartbeat task,
    # so tool_progress frames keep the pause wire-visible for free.
    if agent._tool_gate is not None:
        denial = await gate_tool_call(
            agent._tool_gate,
            ToolApprovalRequest(
                call_id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            ),
        )
        if denial is not None:
            return denial

    # Validate before calling (DESIGN §27.9): a decorated tool's arguments
    # are checked against the schema the model was given and arrive as the
    # signature promises them; a definition the library attached (an MCP
    # server's) only binds — the server validates. Either failure is bad
    # arguments; an error inside the body is the tool failing (TG-7).
    metadata = get_tool_metadata(tool_func)
    try:
        if metadata is not None and metadata.arguments is not None:
            arguments = validate_arguments(metadata.arguments, tool_call.arguments)
        else:
            inspect.signature(tool_func).bind(**tool_call.arguments)
            arguments = dict(tool_call.arguments)
    except (ValidationError, TypeError, ValueError) as e:  # ValueError: no signature
        return rejection(tool_call.name, e)
    try:
        return await tool_func(**arguments)
    except Exception as e:
        return ToolResult.fail(
            ErrorMessages.TOOL_EXECUTION_FAILED.format(
                tool_name=tool_call.name, error=e
            ),
            code=ToolExecutionError.code,
        )


async def execute_tool_with_heartbeats(
    agent: Agent,
    tool_call: ToolCall,
) -> AsyncIterator[tuple[ToolResult[Any] | None, ToolProgressEvent | None]]:
    """Execute a tool while emitting tool_progress events.

    Runs the tool in a background task and emits ToolProgressEvent at
    regular intervals — "this tool is still running", which no keepalive
    means (DESIGN §6).

    Args:
        tool_call: The tool call to execute.

    Yields:
        Tuples of (result, progress):
        - (None, ToolProgressEvent) while the tool is still executing
        - (result, None) when tool execution completes
    """
    start_time = time.monotonic()
    tool_task = asyncio.create_task(execute_tool(agent, tool_call))
    interval = Streaming.HEARTBEAT_INTERVAL_SECONDS

    try:
        while not tool_task.done():
            try:
                # The shield keeps a heartbeat timeout from cancelling the
                # tool; only closing this generator does (below).
                await asyncio.wait_for(asyncio.shield(tool_task), timeout=interval)
            except TimeoutError:
                # Tool still running - emit progress
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                yield (
                    None,
                    ToolProgressEvent(tool_call_id=tool_call.id, elapsed_ms=elapsed_ms),
                )

        # Tool completed - yield result
        yield (tool_task.result(), None)
    finally:
        # Closing this generator — a consumer disconnect, a cancelled
        # wrapper — cancels the tool it shielded and waits it out (AG-1).
        await reap(tool_task)


async def run_tool_stream(
    agent: Agent,
    tool_call: ToolCall,
    queue: asyncio.Queue[AgentEvent | None],
    results: dict[ToolCallId, ToolResult[Any]],
    durations: dict[ToolCallId, int],
    counter: list[int],
    semaphore: asyncio.Semaphore,
) -> None:
    """Run one tool, push progress and result events to the shared queue.

    Used by _stream_with_client to fan out N tool executions and fan in
    their events in completion order. Caller appends Tool messages
    to the attempt's messages in submission order using `results` keyed
    by id, and fires on_tool with the wall time recorded in `durations`.

    The shared `counter` is decremented in `finally`; the last finisher
    pushes a single None sentinel to close the queue. `try/finally`
    guarantees this fires under CancelledError.
    """
    try:
        async with semaphore:
            tool_started = time.monotonic()
            async for result, progress in execute_tool_with_heartbeats(
                agent, tool_call
            ):
                if progress is not None:
                    await queue.put(progress)
                if result is not None:
                    results[tool_call.id] = result
                    durations[tool_call.id] = int(
                        (time.monotonic() - tool_started) * 1000
                    )
                    await queue.put(
                        ToolResultEvent(
                            tool_call_id=tool_call.id,
                            success=result.success,
                            data=result.data if result.success else None,
                            error=result.error if not result.success else None,
                        )
                    )
                    # A memory mutation carries its receipt (NP): the typed
                    # frame follows its tool_result so hosts can render
                    # "remembered X" with undo — never the content.
                    if result.receipt is not None:
                        receipt = result.receipt
                        await queue.put(
                            MemoryWriteEvent(
                                tool_call_id=tool_call.id,
                                command=receipt.command,
                                path=receipt.path,
                                version=receipt.version,
                                previous_path=receipt.previous_path,
                            )
                        )
    finally:
        counter[0] -= 1
        if counter[0] == 0:
            queue.put_nowait(None)


# The clip marker, shaped as `record/span.py`'s: the dropped count, not
# the kept one, so the model reads how much it is missing (NC9, #228).
_TRUNCATED = "\n… [truncated {dropped} chars]"


def _clipped(result: ToolResult[Any], limit: int) -> str:
    """The envelope with its payload cut to fit, still valid JSON.

    The payload is clipped rather than the serialized string, because a
    head-clip of the string would take the closing brace with it — and
    `system_reminder`, which is the repair hint the model needs most when
    a result was too big to send whole. `success` stays first, so
    `FAILED_ENVELOPE_PREFIX` survives for the wire that reads `is_error`
    off it.
    """
    payload = result.data if result.success else result.error
    text = payload if isinstance(payload, str) else safe_json_dumps(payload, "data")

    def envelope(kept: str) -> str:
        marked = kept + _TRUNCATED.format(dropped=len(text) - len(kept))
        clipped = (
            replace(result, data=marked)
            if result.success
            else replace(result, error=marked)
        )
        return clipped.to_json()

    # Measured, never estimated: escaping makes one source character cost
    # one serialized character or six, so no arithmetic on the source
    # length is right for exactly the payloads worth clipping — a page of
    # CJK would lose almost all of it. Bisect on the serialized envelope
    # instead, which is the thing the cap is about. The envelope has a
    # floor — its own braces, the marker, a system_reminder — and a limit
    # under that floor buys an empty payload, never a broken envelope or a
    # lost repair hint: the cap bounds the payload, which is what grows.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(envelope(text[:middle])) <= limit:
            low = middle
        else:
            high = middle - 1
    return envelope(text[:low])


def format_tool_result(result: ToolResult[Any], limit: int | None = None) -> str:
    """Format a tool result as the string the model reads.

    Args:
        result: The tool result to format.
        limit: The model's copy is capped here, marker included; None
            sends it whole. A limit below the envelope's own floor leaves
            an empty payload rather than breaking the JSON. The streamed
            `ToolResultEvent` and the hooks carry the uncapped result
            either way (#228).

    Returns:
        JSON string representation of the result.
    """
    envelope = result.to_json()
    if limit is None or len(envelope) <= limit:
        return envelope
    return _clipped(result, limit)
