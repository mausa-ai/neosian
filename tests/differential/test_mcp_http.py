"""neosian's own MCP client over each door's `/mcp` (R2 slice B, #370,
#371): the SDK's client in its default mode probes the 2026-era
`server/discover`, which the Python door serves through its SDK and the
binary refuses in the spec's own shape, so the client runs the
single-exchange era there and the `initialize` handshake here; either
way the eight tools and a call answer parsed-equal, and the handshake-era
lifecycle a real client drives (the standing GET stream, DELETE on exit)
runs against the binary."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from neosian._foundation.tools.base import get_tool_definition
from neosian.mcp import McpServer
from tests.differential.harness import (
    TOKEN,
    Workspace,
    free_port,
    normalise,
    wait_healthy,
    workspace,
)
from tests.differential.seed import PROJECT_NAME, Seeded


async def _through(executable: Path, space: Workspace, keep: frozenset[str]) -> Any:
    """One door's state process over the workspace, the SDK client through
    its `/mcp`: the tool names, a read and a search, by shape."""
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = space.environment()
    env["NEOSIAN_SERVE_TOKEN"] = TOKEN
    argv = [str(executable), "serve", "--root", str(space.home), "--port", str(port)]
    server = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=space.project,
    )
    try:
        wait_healthy(server, url)
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with McpServer.http(f"{url}/mcp", headers=headers) as remote:
            named: dict[str, Any] = {}
            for tool in remote.tools:
                definition = get_tool_definition(tool)
                assert definition is not None
                named[definition.name] = tool
            viewed = await named["memory"](command="view", path="/")
            searched = await named["search_history"](query="turn")
            missing = await named["memory"](command="view", path="/project/nope")
        return (
            list(named),
            (viewed.success, normalise(str(viewed.data), space, keep=keep)),
            (searched.success, normalise(str(searched.data), space, keep=keep)),
            (missing.success, missing.error),
        )
    finally:
        server.terminate()
        try:
            server.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()


async def test_the_sdk_client_reads_both_doors_alike(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    observed: dict[str, Any] = {}
    for door, executable in (("python", python_door), ("rust", rust_door)):
        space = workspace(
            seeded.home,
            tmp_path / door,
            user=seeded.user,
            proj=seeded.proj,
            session=seeded.sessions[-1],
            project_name=PROJECT_NAME,
        )
        observed[door] = await _through(executable, space, seeded.ids)
    assert observed["rust"] == observed["python"]
    names, (viewed, _), *_ = observed["rust"]
    assert names == [
        "memory",
        "list_skills",
        "load_skill",
        "messages",
        "recall_turn",
        "search_history",
        "continue_session",
        "handoff",
    ]
    assert viewed is True
