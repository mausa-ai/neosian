"""Messages, search and continue select Postgres directly or through either server."""

import json
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from neosian import Message, PostgresStore, Role
from neosian._foundation.memory.sessions import sessions_document
from tests.differential import harness
from tests.support.postgres import store_schema
from tests.support.postgres_server import remote_postgres

SCOPE = "user:read-doors"
CONVERSATION = "read-doors"


@pytest.mark.parametrize("client", ["python", "rust"])
@pytest.mark.parametrize("transport", ["direct", "python", "rust"])
async def test_mailbox_and_history_on_every_postgres_path(
    client: str,
    transport: str,
    store: PostgresStore,
    postgres_dsn: str,
    tmp_path: Path,
) -> None:
    binary = harness.python_door() if client == "python" else harness.rust_door()
    if binary is None:
        pytest.skip("no binary: cargo build --locked or set NEOSIAN_BINARY")
    turn = await store.append_turn(
        CONVERSATION,
        [Message(Role.USER, "needle in the recorded turn")],
        actor="cli:source",
    )
    listing = await store.write(
        SCOPE,
        f"sessions/{CONVERSATION}",
        sessions_document(
            agent="source",
            session_id=CONVERSATION,
            started=turn.created_at,
            last_prompt="needle",
            turns=1,
        ),
    )
    context = (
        nullcontext(None)
        if transport == "direct"
        else remote_postgres(
            store, postgres_dsn, tmp_path, transport, "reader", over_socket=True
        )
    )
    async with context as remote:
        env = {k: v for k, v in os.environ.items() if not k.startswith("NEOSIAN_")}
        env["NEOSIAN_HOME"] = str(tmp_path / "unused-home")
        if remote is None:
            env["NEOSIAN_POSTGRES_DSN"] = postgres_dsn
            selection = ["--schema", store_schema(store)]
            expected_client = None
        else:
            env["NEOSIAN_CLIENT_TOKEN"] = "reader"
            selection = ["--url", remote._base_url]  # noqa: SLF001 - process fixture
            expected_client = "client:default"

        def invoke(*args: str) -> dict[str, Any]:
            result = subprocess.run(
                [str(binary), *args, *selection, "--json"],
                env=env,
                cwd=tmp_path,
                text=True,
                capture_output=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            data: dict[str, Any] = json.loads(result.stdout)
            return data

        sent = json.loads(
            invoke(
                "messages",
                "send",
                "--scope",
                SCOPE,
                "--body",
                "needle in a message",
                "--about",
                CONVERSATION,
                "--about-turn",
                "1",
            )["data"]
        )
        result = invoke("search", "needle", "--conversation", CONVERSATION)
        assert result["client"] == expected_client
        assert {hit.get("type", "turn") for hit in result["hits"]} == {
            "turn",
            "message",
        }
        assert any(hit.get("annotations") for hit in result["hits"])
        for named in ((), (CONVERSATION,)):
            result = invoke("continue", *named, "--scope", SCOPE)
            assert result["client"] == expected_client
            assert result["conversation"] == CONVERSATION
            assert "needle in the recorded turn" in result["text"]
        acknowledged = json.loads(
            invoke(
                "messages",
                "ack",
                "--scope",
                SCOPE,
                "--message-id",
                sent["id"],
                "--session",
                "reader",
                "--occurrence",
                "1",
            )["data"]
        )
        assert acknowledged["read_by"] == "reader"
        assert await store.read(SCOPE, f"sessions/{CONVERSATION}") == listing
        assert not (tmp_path / "unused-home").exists()
