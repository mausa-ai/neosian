"""A neosian agent consuming an MCP server as tools (DESIGN §25).

`McpServer.stdio(...)` spawns the server and connects through the
official MCP client for the lifetime of the `async with`; its tools are
plain tool functions for `AgentConfig(tools=[*server.tools])`. The
server here is neosian's own memory server on a scratch root — the
same store the agent could reach in-process — so the round trip is
honest: the schema crosses verbatim, the result comes back as MCP's
`is_error` read into a `ToolResult`, and nothing about the agent knows
it is talking over pipes. Two runs: the first remembers, the second
recalls from a fresh process.

Usage:
    CEREBRAS_API_KEY=... python examples/mcp_client_example.py
"""

import asyncio
import sys
from pathlib import Path

from neosian import Agent, AgentConfig, Message, Model, Role
from neosian.mcp import McpServer

ROOT = Path(".neosian/example-mcp")
SERVER_ARGS = ["-m", "neosian.mcp", "--root", str(ROOT), "--scope", "user:example"]


async def ask(prompt: str) -> None:
    async with McpServer.stdio(sys.executable, SERVER_ARGS) as server:
        print(f"[{server.name}] tools:", [t.__name__ for t in server.tools])
        agent = Agent(
            AgentConfig(
                system_prompt=(
                    "You keep notes for the user with the memory tool. "
                    "Write facts down as soon as you learn them, and look "
                    "them up before answering questions about the user."
                ),
                model=Model.CEREBRAS_GPT_OSS_120B,
                tools=[*server.tools],
                enable_todo=False,
            )
        )
        response = await agent.run(
            [Message(role=Role.USER, content=prompt)], stream=False
        )
        for call in response.tool_calls_made:
            print(
                "  tool:",
                call.name,
                call.arguments.get("command"),
                call.arguments.get("path"),
            )
        print("agent:", response.message.content)


async def main() -> None:
    await ask("Remember: I take my espresso with no sugar, and my dog is called Maki.")
    await ask("What is my dog called?")


if __name__ == "__main__":
    asyncio.run(main())
