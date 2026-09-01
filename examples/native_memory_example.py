"""Anthropic's native memory tool over the same store (N4, ledger #19).

`native_memory=True` swaps the transport, never the memory: the
Anthropic converter declares `memory_20250818` in place of the function
schema, and the model's trained memory behavior drives the same six
commands into the same FileStore, under the same mounts and receipts.
A bare Agent (no Conversation) writes natively here; the function tool
then reads the document back from the same root — one store.

Usage:
    ANTHROPIC_API_KEY=... python examples/native_memory_example.py
"""

import asyncio
from pathlib import Path

from neosian import (
    Agent,
    AgentConfig,
    FileStore,
    MemoryConfig,
    Message,
    Model,
    Mount,
    Role,
    create_memory_tool,
    memory_system_section,
)

_ROOT = Path(__file__).resolve().parent.parent / ".neosian" / "tour"
_MEMORY = MemoryConfig(
    store=FileStore(_ROOT),
    mounts=[
        Mount(
            scope="user:tour",
            mount_path="memories",
            description="Durable facts about the user and their team.",
        )
    ],
)


async def main() -> None:
    # The frozen index is the caller's to inject (Conversation does it
    # for you); a bare Agent shows the seam.
    section = await memory_system_section(_MEMORY)
    agent = Agent(
        AgentConfig(
            system_prompt="You are a concise assistant.\n\n" + section,
            model=Model.CLAUDE_SONNET_5,
            memory=_MEMORY,
            native_memory=True,
            enable_todo=False,
        )
    )
    response = await agent.run(
        [
            Message(
                role=Role.USER,
                content=(
                    "Please note for later: my editor is Neovim and I keep my "
                    "dotfiles in ~/dots."
                ),
            )
        ],
        stream=False,
    )
    calls = [(c.name, c.arguments.get("command")) for c in response.tool_calls_made]
    print(f"tools: {calls}")
    print(f"agent: {response.message.content}")

    print("--- the function tool reads the same root")
    view = await create_memory_tool(_MEMORY)(command="view", path="/memories")
    print(view.data)


if __name__ == "__main__":
    asyncio.run(main())
