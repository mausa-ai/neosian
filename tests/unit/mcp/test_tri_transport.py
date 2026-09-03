"""The N4 done-when, grown at NA and NM: one store serves the same
memory through the function tool, the native flag, an MCP client, the
memory CLI's engine, and the state process's streamable-HTTP door —
five transports, one dispatcher (DESIGN §18.6)."""

import io
import json
from pathlib import Path
from typing import Any

import httpx
from mcp.client import Client

from neosian._foundation.mcp.server import create_memory_server
from neosian._foundation.memory.cli import run
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.settings import format_mount
from neosian._foundation.memory.tools import (
    NATIVE_MEMORY_TOOL_TYPE,
    create_memory_tool,
)
from neosian._foundation.record.cli import run as record
from neosian._foundation.server.app import build_app
from neosian._foundation.tools.base import get_tool_definition
from tests.unit.record.payloads import SESSION, prompt, stop

_TOKEN = "five-transports"
_HEADERS = {
    "Authorization": f"Bearer {_TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "neosian-tests", "version": "0"},
    },
}
_INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "memory",
        "arguments": {
            "command": "create",
            "path": "/memories/from-http",
            "content": "hi over http",
        },
    },
}


def _sse_payload(body: str) -> dict[str, Any]:
    """The one JSON-RPC message out of a streamable-HTTP SSE response."""
    for line in body.splitlines():
        if line.startswith("data: "):
            decoded: dict[str, Any] = json.loads(line[len("data: ") :])
            return decoded
    raise AssertionError(f"no SSE data frame in {body!r}")


async def test_one_store_five_transports(
    config: MemoryConfig, store: FileStore, tmp_path: Path
) -> None:
    # 1. The function tool writes.
    plain = create_memory_tool(config)
    created = await plain(command="create", path="/memories/prefs", content="dark mode")
    assert created.success

    # 1b. A foreign agent's hooks write a turn into the same root (NL's
    #     record verb) — what step 3 recalls over MCP (NB, §21.7).
    record_argv = ["--root", str(tmp_path / "memory"), "--spool", str(tmp_path / "sp")]
    for mount in config.mounts:
        record_argv += ["--mount", format_mount(mount)]
    for payload in (prompt("hooked hello"), stop("hooked done")):
        out, err = io.StringIO(), io.StringIO()
        code = await record(
            record_argv, {}, stdin=io.StringIO(json.dumps(payload)), out=out, err=err
        )
        assert code == 0, err.getvalue()

    # 2. The native-marked tool reads the same bytes (slice A pins that
    #    marked and unmarked execution are identical; here we pin the
    #    marker and the read against this store).
    native = create_memory_tool(config, native=True)
    definition = get_tool_definition(native)
    assert definition is not None
    assert definition.native_type == NATIVE_MEMORY_TOOL_TYPE
    native_view = await native(command="view", path="/memories/prefs")
    assert native_view.success
    assert "dark mode" in str(native_view.data)

    # 3. An MCP client reads the same document and writes a second one —
    #    and recalls the hook-fed turn verbatim: any agent, the record.
    server = await create_memory_server(config, conversations=store)
    async with Client(server) as client:
        mcp_view = await client.call_tool(
            "memory", {"command": "view", "path": "/memories/prefs"}
        )
        assert mcp_view.is_error is False
        assert mcp_view.content[0].text == native_view.data  # type: ignore[union-attr]

        recalled = await client.call_tool(
            "recall_turn", {"turn": 1, "conversation": SESSION}
        )
        assert recalled.is_error is False
        assert "USER: hooked hello" in recalled.content[0].text  # type: ignore[union-attr]

        mcp_create = await client.call_tool(
            "memory",
            {"command": "create", "path": "/memories/from-mcp", "content": "hi"},
        )
        assert mcp_create.is_error is False

    # 4. The CLI engine (the shell transport) writes to the same root.
    out, err = io.StringIO(), io.StringIO()
    argv = ["create", "/memories/from-cli", "--content", "hi from the shell"]
    argv += ["--root", str(tmp_path / "memory"), "--actor", "cli:local"]
    for mount in config.mounts:
        argv += ["--mount", format_mount(mount)]
    code = await run(argv, {}, stdin=io.StringIO(), out=out, err=err)
    assert code == 0

    # 5. MCP over streamable HTTP — the state process's agent door
    #    (§18.6): the same factory's server at /mcp, driven with raw
    #    JSON-RPC over the ASGI transport (the SDK's client needs a real
    #    socket). The lifespan is entered in this task deliberately —
    #    the session manager's task group refuses to exit anywhere else.
    app = await build_app(
        FileStore(tmp_path / "memory"),
        token=_TOKEN,
        mounts=config.mounts,
        actor="serve:test",
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://state-process"
        ) as http,
    ):
        init = await http.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
        assert init.status_code == 200
        session = {"mcp-session-id": init.headers["mcp-session-id"]}
        note = await http.post("/mcp", json=_INITIALIZED, headers=_HEADERS | session)
        assert note.status_code in (200, 202)
        called = await http.post("/mcp", json=_CALL, headers=_HEADERS | session)
        assert called.status_code == 200
        result = _sse_payload(called.text)["result"]
        assert result.get("isError") is not True

    # 6. The function tool sees every transport's write in the index.
    index = await plain(command="view", path="/")
    assert index.success
    assert "prefs" in str(index.data)
    assert "from-mcp" in str(index.data)
    assert "from-cli" in str(index.data)
    assert "from-http" in str(index.data)
