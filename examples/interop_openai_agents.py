"""Neosian memory under an OpenAI Agents SDK agent.

Usage:
    OPENAI_API_KEY=... uv run python examples/interop_openai_agents.py \\
        "Remember that I take my tea without milk." [MODEL]

The memory tool neosian builds is one definition and one executor
(`neosian docs interop`): `tool_definition` hands the SDK the schema as a
`FunctionTool`, the tool itself runs each call, and `ToolResult.to_json()`
is the string the model reads back. Keyless by construction: the unit
tier drives `run` with a scripted `Model`. A real model is named on the
command line and found through the environment alone (`OPENAI_API_KEY`;
with `OPENAI_BASE_URL` set, a local or compatible server on Chat
Completions, `neosian docs local`).

The data stays yours: `memory/` is a directory of markdown files you can
cat, grep, git and leave with.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from agents import (
    Agent,
    FunctionTool,
    Model,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
)
from agents.tool_context import ToolContext
from openai import AsyncOpenAI

from neosian import (
    FileStore,
    MemoryConfig,
    Mount,
    ToolResult,
    create_memory_tool,
    memory_system_section,
    tool_definition,
)

MOUNTS = (
    Mount(scope="user:demo", mount_path="user", description="Facts about the user"),
)
ACTOR = "openai-agents:demo"  # the version rows name who wrote them


async def build(root: Path) -> tuple[FunctionTool, str]:
    """The memory tool as a `FunctionTool`, and the instructions carrying its index."""
    config = MemoryConfig(store=FileStore(root), mounts=MOUNTS)
    memory = create_memory_tool(config, actor=ACTOR)
    definition = tool_definition(memory)

    async def on_invoke(_context: ToolContext[Any], arguments_json: str) -> str:
        arguments: dict[str, Any] = json.loads(arguments_json) if arguments_json else {}
        try:
            result = await memory(**arguments)
        except Exception as exc:  # a mistyped value; the model self-corrects
            result = ToolResult.fail(f"Tool '{definition.name}' failed: {exc}")
        return result.to_json()

    tool = FunctionTool(
        name=definition.name,
        description=definition.description,
        params_json_schema=definition.parameters,
        on_invoke_tool=on_invoke,
        strict_json_schema=False,  # strict would rewrite the optional fields
    )
    return tool, await memory_system_section(config)


async def run(model: Model | str, prompt: str, *, root: Path) -> str:
    """One turn: the model may call `memory`, then answers."""
    tool, instructions = await build(root)
    agent = Agent(name="memory", instructions=instructions, tools=[tool], model=model)
    result = await Runner.run(
        agent, prompt, run_config=RunConfig(tracing_disabled=True)
    )
    return str(result.final_output)


async def main(argv: list[str]) -> None:
    prompt = argv[1] if len(argv) > 1 else "Remember that I take my tea without milk."
    model: Model | str = argv[2] if len(argv) > 2 else "gpt-6-sol"
    if os.environ.get("OPENAI_BASE_URL"):  # a compatible server speaks Chat Completions
        model = OpenAIChatCompletionsModel(
            model=str(model), openai_client=AsyncOpenAI()
        )
    print(await run(model, prompt, root=Path("memory")))


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
