"""Remote forms of the existing corpus: identical cases through all four
client/server pairs, plus a full recorded span read back over HTTP."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from tests.differential.corpus import CASES, RECORD, SESSION
from tests.differential.harness import TOKEN, Case, Workspace, free_port, wait_healthy
from tests.unit.record.payloads import prompt, session_start, stop, tool

REMOTE_CASES = tuple(case for case in CASES if case.verb in {"record", "mcp"})
SPAN = (
    Case("remote prompt", (*RECORD, "--json"), payload=prompt(session=SESSION)),
    Case("remote tool", (*RECORD, "--json"), payload=tool(session=SESSION)),
    Case("remote stop", (*RECORD, "--json"), payload=stop(session=SESSION)),
    Case(
        "remote startup",
        (*RECORD, "--json"),
        payload=session_start(session=SESSION),
    ),
)


def remote_case(case: Case, url: str) -> Case:
    argv = list(case.argv)
    at = argv.index("--root")
    argv[at : at + 2] = ["--url", url]
    return replace(
        case, argv=tuple(argv), env={**case.env, "NEOSIAN_CLIENT_TOKEN": TOKEN}
    )


@contextmanager
def serving(
    executable: Path, space: Workspace, *, port: int | None = None
) -> Iterator[str]:
    port = free_port() if port is None else port
    url = f"http://127.0.0.1:{port}"
    env = {**space.environment(), "NEOSIAN_SERVE_TOKEN": TOKEN}
    server = subprocess.Popen(
        [str(executable), "serve", "--root", str(space.home), "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=space.project,
        env=env,
    )
    try:
        wait_healthy(server, url)
        yield url
    finally:
        server.terminate()
        try:
            server.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()


def _memory_lifecycle() -> Case:
    from tests.differential.corpus import MOUNTS

    initialize = REMOTE_CASES[-1].rpc[:2]
    operations = (
        {"command": "create", "path": "/project/remote", "file_text": "first\nlast"},
        {
            "command": "str_replace",
            "path": "/project/remote",
            "old_str": "first",
            "new_str": "changed",
        },
        {
            "command": "insert",
            "path": "/project/remote",
            "insert_line": 1,
            "new_str": "middle",
        },
        {
            "command": "rename",
            "old_path": "/project/remote",
            "new_path": "/project/moved",
        },
        {"command": "rename", "old_path": "/project/moved", "new_path": "/user/moved"},
        {"command": "view", "path": "/user/moved"},
        {"command": "delete", "path": "/user/moved"},
        {"command": "delete", "path": "/user/moved"},
        {
            "command": "rename",
            "old_path": "/project/facts",
            "new_path": "/project/skills/release",
        },
    )
    calls = tuple(
        {
            "jsonrpc": "2.0",
            "id": n,
            "method": "tools/call",
            "params": {"name": "memory", "arguments": args},
        }
        for n, args in enumerate(operations, 2)
    )
    return Case(
        "remote memory lifecycle",
        ("mcp", *MOUNTS),
        rpc=(*initialize, *calls),
        writes=True,
    )


REMOTE_CASES = (*REMOTE_CASES, _memory_lifecycle())
