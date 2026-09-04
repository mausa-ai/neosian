"""MCP, both directions: the memory server and the client (DESIGN §25).

The server is the third transport for the one memory tool — the
function tool, the native `memory_20250818` declaration, and this
server all execute through `memory/dispatch.py`; the server serves the
function tool's `ToolDefinition` verbatim, so the three surfaces cannot
drift (DESIGN §8, ledger #50). The client (`client.py`, `bridge.py`)
is `McpServer`: any MCP server's tools as ordinary tool functions for
the caller's lifetime (NC1).

Importing this package never imports the MCP SDK — that happens in
`sdk.py`, function-locally, on first use (the postgres driver pattern).
This package never touches `_foundation.agent` or provider internals:
it is store + tool layer only (import-linter contract).
"""
