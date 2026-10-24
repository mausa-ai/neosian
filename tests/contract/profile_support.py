"""Literal portable-workspace inputs; no product parser or store imports."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.contract.support import Binary

WORKSPACE = "11111111-2222-4333-8444-555555555555"
PREFIX = "w-11111111222243338444555555555555-"
PROFILE = f"""profile_format = 1
workspace_id = "{WORKSPACE}"
name = "Portable knowledge"
sources = ["knowledge"]
skills = ["/project/skills/review"]

[[mounts]]
source = "knowledge"
scope = "workspace:portable"
path = "project"
access = "rw"
injection = "index"
"""
GRANTS = f"""grant_format = 1
[[clients]]
actor = "client:contract"
operator = true
[[clients.scopes]]
scope = "workspace:portable"
read = true
write = true
[[clients.scopes]]
scope = "workspace:other"
read = true
write = true
[[clients.conversations]]
prefix = "{PREFIX}"
read = true
append = true
[[clients.conversations]]
prefix = "other-"
read = true
append = true

[[clients]]
actor = "client:reader"
[[clients.scopes]]
scope = "workspace:portable"
read = true
[[clients.conversations]]
prefix = "{PREFIX}"
read = true
"""


def run(
    binary: Binary, *args: str, env: dict[str, str] | None = None, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary.executable), *args],
        cwd=binary.directory,
        env={**binary.environment(), **(env or {})},
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def install(binary: Binary, text: str = PROFILE, name: str = "portable") -> Any:
    path = binary.directory / f"{name}-import.toml"
    path.write_text(text, encoding="utf-8")
    result = binary.run("profile", "import", name, str(path), "--write", "--replace")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def bind(binary: Binary, *args: str, source: str = "knowledge") -> Any:
    result = binary.run(
        "profile", "bind", "portable", "--source", source, *args, "--write"
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def memory(binary: Binary, command: str, path: str, *args: str) -> Any:
    return binary.json("memory", command, path, "--profile", "portable", *args)


def rpc(
    binary: Binary,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    env: dict[str, str] | None = None,
    actor: str = "mcp:contract",
) -> list[Any]:
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": actor, "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ]
    for number, (name, arguments) in enumerate(calls or [], 2):
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
    process = run(
        binary,
        "mcp",
        "--profile",
        "portable",
        "--actor",
        actor,
        env=env,
        stdin="".join(json.dumps(r) + "\n" for r in requests),
    )
    assert process.returncode == 0, process.stderr
    return [json.loads(line) for line in process.stdout.splitlines()]


def grant_env(path: Path) -> dict[str, str]:
    path.write_text(GRANTS, encoding="utf-8")
    return {
        "NEOSIAN_SERVE_GRANTS": str(path),
        "NEOSIAN_SERVE_TOKEN": "client:contract=contract-admin,client:reader=reader-token",
    }
