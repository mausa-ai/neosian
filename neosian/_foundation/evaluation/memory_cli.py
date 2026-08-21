"""The `cli` transport — the shell surface, executed in-process (#78).

A cli cell's memory tool converts each call's arguments to argv, runs
the real `neosian memory` engine — grammar, a fresh store built from
`--root` on every invocation (a real process's cold-store property),
dispatch, the `--json` envelope — inside the harness's own loop, and
parses the envelope back into a `ToolResult`. Everything that can drift
between the function tool and the shell (flag spelling, the envelope,
the exit tiering) is crossed on every call; the OS process boundary
adds no memory semantics and is pinned once by the keyless walkthrough
(§14.3 states the limit).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from neosian._foundation.memory.cli import ARGUMENT_KEYS, run
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.settings import format_mount
from neosian._foundation.memory.tools import build_memory_tool
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import ToolResult

_POSITIONALS = ("path", "old_path", "new_path")


def create_cli_memory_tool(
    *, store_root: Path, mounts: tuple[Mount, ...], actor: str
) -> ToolFunction:
    """The `memory` tool (one wire definition) executed through the shell."""
    store_flags = ["--root", str(store_root)]
    for mount in mounts:
        store_flags += ["--mount", format_mount(mount)]
    store_flags += ["--actor", actor, "--json"]

    async def execute(command: object, arguments: dict[str, Any]) -> ToolResult[str]:
        out, err = io.StringIO(), io.StringIO()
        # env={} — never os.environ: an exported NEOSIAN_POSTGRES_DSN
        # would collide with --root and kill every cell.
        code = await run(
            _argv(command, arguments) + store_flags,
            {},
            stdin=io.StringIO(),
            out=out,
            err=err,
        )
        return _decode(code, out.getvalue(), err.getvalue())

    return build_memory_tool(execute)


def _argv(command: object, arguments: dict[str, Any]) -> list[str]:
    """Arguments → argv, mirroring the engine's own key list.

    `None` values are omitted — a call missing a required argument gets
    the grammar's own exit-2 answer, the honest shell behavior. Unknown
    commands go through bare: the grammar names the six.
    """
    name = command if isinstance(command, str) else str(command)
    argv = [name]
    keys = ARGUMENT_KEYS.get(name)
    if keys is None:
        return argv
    values = dict(arguments)
    if "content" in keys and values.get("content") is None:
        # dispatch's trained `file_text` alias, resolved at the argv
        # boundary — the grammar itself has no alias flag (§14.2).
        values["content"] = values.get("file_text")
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        if key in _POSITIONALS:
            argv.append(str(value))
        elif key == "view_range":
            argv += ["--view-range", *(str(v) for v in value)]
        else:
            argv += [f"--{key.replace('_', '-')}", str(value)]
    return argv


def _decode(code: int, stdout: str, stderr: str) -> ToolResult[str]:
    if code in (0, 1):
        # The envelope is asymmetric: `data` only on success, `error`
        # only on failure, `system_reminder` optional.
        payload: dict[str, Any] = json.loads(stdout)
        reminder = payload.get("system_reminder")
        if payload.get("success"):
            data = payload.get("data")
            return ToolResult.ok("" if data is None else data, system_reminder=reminder)
        return ToolResult.fail(str(payload.get("error")), system_reminder=reminder)
    text = stderr.strip()
    return ToolResult.fail(
        text or f"neosian memory exited {code}",
        system_reminder="Run `neosian memory --help` for the command grammar.",
    )
