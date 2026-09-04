"""Public MCP surface: serve neosian's state to any MCP client, and
consume any MCP server as tools.

`python -m neosian.mcp --root PATH --scope user:me` serves a FileStore
on stdio — the `memory` tool, the skills, and `recall_turn` over its
conversations; `create_memory_server` is the factory for hosts that
embed the server (own store, mount descriptions, own transport;
`conversations=` adds the recall tool). `McpServer` is the other
direction (DESIGN §25): `async with McpServer.stdio(...) as server` —
or `.http(url)`, or `.in_process(server)` — connects through the
official client and exposes the server's tools as plain tool functions
for `AgentConfig(tools=[*server.tools])`. The root package never imports
this module; it loads only when you do — and importing it does not load
the MCP SDK (that happens on first use, `mcp` extra).
"""

from neosian._foundation.mcp.client import McpServer
from neosian._foundation.mcp.server import create_memory_server

__all__ = ["McpServer", "create_memory_server"]
