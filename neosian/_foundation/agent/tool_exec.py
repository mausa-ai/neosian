"""Tool execution: the gated single call, heartbeats, and the
streaming fan-out worker."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from neosian._foundation.agent.approval import ToolApprovalRequest, gate_tool_call
from neosian._foundation.agent.events import (
    AgentEvent,
    MemoryWriteEvent,
    ToolProgressEvent,
    ToolResultEvent,
)
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.shared.constants import ErrorMessages, Streaming
from neosian._foundation.shared.types import ToolCallId
from neosian._foundation.tools.base import ToolResult

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

    try:
        result = await tool_func(**tool_call.arguments)
        return result
    except TypeError as e:
        return ToolResult.fail(
            ErrorMessages.TOOL_INVALID_ARGUMENTS.format(
                tool_name=tool_call.name, error=e
            )
        )
    except Exception as e:
        return ToolResult.fail(
            ErrorMessages.TOOL_EXECUTION_FAILED.format(
                tool_name=tool_call.name, error=e
            )
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

    while not tool_task.done():
        try:
            # Wait for tool to complete or timeout
            await asyncio.wait_for(
                asyncio.shield(tool_task),
                timeout=interval,
            )
        except TimeoutError:
            # Tool still running - emit progress
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            yield (
                None,
                ToolProgressEvent(tool_call_id=tool_call.id, elapsed_ms=elapsed_ms),
            )

    # Tool completed - yield result
    yield (tool_task.result(), None)


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


def format_tool_result(result: ToolResult[Any]) -> str:
    """Format a tool result as a string for the LLM.

    Args:
        result: The tool result to format.

    Returns:
        JSON string representation of the result.
    """
    return result.to_json()
