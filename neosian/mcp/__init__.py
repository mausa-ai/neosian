"""Public MCP surface: serve neosian memory to any MCP client.

`python -m neosian.mcp --root PATH --scope user:me` serves a FileStore
on stdio; `create_memory_server` is the factory for hosts that embed the
server (own store, mount descriptions, own transport). The root package
never imports this module; it loads only when you do — and importing it
does not load the MCP SDK (that happens on first use, `mcp` extra).
"""

from neosian._foundation.mcp.server import create_memory_server

__all__ = ["create_memory_server"]
