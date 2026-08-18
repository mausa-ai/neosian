"""Demo agent for parallel tool execution.

Single `sleep` tool that logs start/end timestamps. Ask the agent to
"sleep 10 seconds twice" in the playground and watch the timestamps —
both calls should start near-simultaneously and finish ~10s later (not 20s).

Usage:
    uv run neosian playground examples/parallel_sleep_agent.py
"""

import asyncio
import time

from neosian import (
    AgentConfig,
    Model,
    Tool,
    ToolResult,
)

_START = time.monotonic()


def _ts() -> str:
    return f"t+{time.monotonic() - _START:5.2f}s"


@Tool(
    name="sleep",
    description=(
        "Sleep for the given number of seconds. If the user asks to sleep N times, "
        "emit N parallel tool calls in a single response — do NOT chain them."
    ),
)
async def sleep(seconds: float) -> ToolResult[str]:
    """Sleep for `seconds` seconds, logging start and end timestamps."""
    print(f"  [{_ts()}] sleep({seconds}s) started")
    await asyncio.sleep(seconds)
    print(f"  [{_ts()}] sleep({seconds}s) finished")
    return ToolResult.ok(f"slept {seconds}s")


configuration = AgentConfig(
    system_prompt=(
        "You have one tool: `sleep`. When the user asks you to sleep N times, "
        "ALWAYS emit all N tool calls in a single response (parallel), never one "
        "at a time across turns. Then summarize what you did."
    ),
    tools=[sleep],
    model=Model.GROQ_GPT_OSS_20B,
    enable_todo=False,
)
