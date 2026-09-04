"""The `mcp` transport — the MCP door under the model (NC1, DESIGN §25).

An mcp cell's tools are the memory server's own — `memory`, the skills
pair, `recall_turn` — consumed through `McpServer.in_process` over the
official client: every tool call crosses the MCP wire (the request,
the `is_error` mapping, the content blocks) exactly as a third-party
agent consumes the daemon's `/mcp`, keyless and port-free. The
session's index render, reflection and maintenance stay on the local
store handle (the cli column's split), so the cells measure the door
under live traffic, never the store. Importing this module never loads
the SDK; an mcp cell without the extra fails with the install hint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.mcp.client import McpServer
from neosian._foundation.mcp.server import create_memory_server
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.shared.types import ToolFunction


@asynccontextmanager
async def open_mcp_tools(
    config: MemoryConfig, *, actor: str
) -> AsyncIterator[tuple[ToolFunction, ...]]:
    """The memory server on the cell's store handle, consumed as tools —
    `recall_turn` included when the store holds conversations, the stdio
    entry point's rule."""
    store = config.store
    conversations = store if isinstance(store, ConversationStore) else None
    server = await create_memory_server(
        config, actor=actor, conversations=conversations
    )
    async with McpServer.in_process(server) as bridge:
        yield bridge.tools
