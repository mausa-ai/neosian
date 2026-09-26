"""Neosian memory under a pydantic-ai agent.

Usage:
    OPENAI_API_KEY=... uv run python examples/interop_pydantic_ai.py \\
        "Remember that I take my tea without milk." [openai:MODEL]

The memory tool neosian builds is one definition and one executor
(`neosian docs interop`): `tool_definition` hands pydantic-ai the schema,
the tool itself runs each call, and `ToolResult.to_json()` is the string
the model reads back. Keyless by construction: the unit tier drives `run`
with pydantic-ai's `FunctionModel`. A real model is named on the command
line and found through the environment alone (`OPENAI_API_KEY`, or
`OPENAI_BASE_URL` for a local server, `neosian docs local`).

The data stays yours: `memory/` is a directory of markdown files you can
cat, grep, git and leave with.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, Tool
from pydantic_ai.models import Model

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
ACTOR = "pydantic-ai:demo"  # the version rows name who wrote them


async def build(root: Path) -> tuple[Tool[None], str]:
    """The memory tool as a pydantic-ai tool, and the instructions carrying its index."""
    config = MemoryConfig(store=FileStore(root), mounts=MOUNTS)
    memory = create_memory_tool(config, actor=ACTOR)
    definition = tool_definition(memory)

    async def call(**arguments: Any) -> str:
        try:
            result = await memory(**arguments)
        except Exception as exc:  # a mistyped value; the model self-corrects
            result = ToolResult.fail(f"Tool '{definition.name}' failed: {exc}")
        return result.to_json()

    tool = Tool.from_schema(
        call,
        name=definition.name,
        description=definition.description,
        json_schema=definition.parameters,
    )
    return tool, await memory_system_section(config)


async def run(model: Model | str, prompt: str, *, root: Path) -> str:
    """One turn: the model may call `memory`, then answers."""
    tool, instructions = await build(root)
    agent: Agent[None, str] = Agent(model, instructions=instructions, tools=[tool])
    result = await agent.run(prompt)
    return result.output


async def main(argv: list[str]) -> None:
    prompt = argv[1] if len(argv) > 1 else "Remember that I take my tea without milk."
    model = argv[2] if len(argv) > 2 else "openai:gpt-6-sol"
    print(await run(model, prompt, root=Path("memory")))


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
