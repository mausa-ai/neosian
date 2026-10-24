"""Direct binary doors select Postgres without involving the Python server."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from neosian import PostgresStore
from neosian._foundation.tools.base import get_tool_definition
from neosian.mcp import McpServer
from tests.differential.harness import rust_door
from tests.support.postgres import fetch_sql, store_schema
from tests.unit.record.payloads import prompt, stop


@pytest.fixture
def binary() -> Path:
    binary = rust_door()
    if binary is None:
        pytest.skip("no binary: `cargo build --locked` or set NEOSIAN_BINARY")
    return binary


@pytest.fixture
def environment(postgres_dsn: str, tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEOSIAN_")}
    env.update(NEOSIAN_POSTGRES_DSN=postgres_dsn, NEOSIAN_HOME=str(tmp_path / "home"))
    return env


async def test_record_lands_turns_and_clears_only_a_successful_spool(
    binary: Path, environment: dict[str, str], store: PostgresStore, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    argv = [
        str(binary),
        "record",
        "--schema",
        store_schema(store),
        "--scope",
        "user:hooks",
        "--spool",
        str(spool),
        "--json",
    ]
    for payload in (prompt(session="pg-hook"), stop(session="pg-hook")):
        completed = subprocess.run(
            argv,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=environment,
            cwd=tmp_path,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
    turns = await store.read_turns("pg-hook")
    assert len(turns) == 1
    assert turns[0].actor == "claude-code:pg-hook"
    assert turns[0].messages[0].content == "Add a test for login"
    assert await store.read("user:hooks", "sessions/pg-hook") is not None
    assert not (spool / "pg-hook.jsonl").exists()


async def test_stdio_mcp_writes_the_same_schema(
    binary: Path, environment: dict[str, str], store: PostgresStore, tmp_path: Path
) -> None:
    args = ["mcp", "--schema", store_schema(store), "--scope", "user:mcp"]
    async with McpServer.stdio(
        str(binary), args, env=environment, cwd=str(tmp_path)
    ) as server:
        for tool in server.tools:
            definition = get_tool_definition(tool)
            if definition is not None and definition.name == "memory":
                result = await tool(
                    command="create", path="/memories/doc", content="over stdio"
                )
                assert result.success
                break
        else:
            pytest.fail("memory tool is absent")
    document = await store.read("user:mcp", "doc")
    assert document is not None and document.content == "over stdio"
    assert document.actor == "mcp:stdio"


async def test_memory_and_operator_doors_use_the_selected_schema(
    binary: Path, environment: dict[str, str], store: PostgresStore, tmp_path: Path
) -> None:
    for argv in (
        [
            "memory",
            "create",
            "/memories/doc",
            "--content",
            "direct",
            "--scope",
            "user:cli",
        ],
        ["audit", "--scope", "user:cli"],
        ["export", str(tmp_path / "archive"), "--scope", "user:cli"],
    ):
        result = subprocess.run(
            [str(binary), *argv, "--schema", store_schema(store), "--json"],
            text=True,
            capture_output=True,
            env=environment,
            cwd=tmp_path,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert isinstance(json.loads(result.stdout), dict)
    document = await store.read("user:cli", "doc")
    assert document is not None and document.content == "direct"


async def test_require_tls_refuses_a_plaintext_database_without_exposing_credentials(
    binary: Path, environment: dict[str, str], store: PostgresStore, tmp_path: Path
) -> None:
    # Probe only a plaintext server; an operator may supply a TLS-enabled DSN.
    if await fetch_sql(store, "SHOW ssl", {}) != [("off",)]:
        pytest.skip("the supplied database supports TLS")
    if "sslmode=" in environment["NEOSIAN_POSTGRES_DSN"]:
        pytest.skip("the supplied database has an explicit TLS policy")
    dsn = environment["NEOSIAN_POSTGRES_DSN"]
    if not dsn.startswith(("postgresql://", "postgres://")):
        pytest.skip("this refusal probe needs a URI DSN")
    env = {
        **environment,
        "NEOSIAN_POSTGRES_DSN": dsn + ("&" if "?" in dsn else "?") + "sslmode=require",
    }
    result = subprocess.run(
        [
            str(binary),
            "memory",
            "view",
            "/",
            "--schema",
            store_schema(store),
            "--scope",
            "user:tls",
            "--json",
        ],
        text=True,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 1
    assert "Postgres request failed" in result.stdout + result.stderr
    assert dsn not in result.stdout + result.stderr
