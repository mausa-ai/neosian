"""Actual server page boundaries through the MCP client's shared readers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from neosian import FileStore, Message, Role
from neosian.conversation import ConversationProjection
from tests.differential.harness import Case, run_case, shape_of_output, workspace
from tests.differential.remote_cases import REMOTE_CASES, remote_case, serving
from tests.differential.seed import PROJECT_NAME, Seeded


async def test_large_listings_turns_and_whole_turn_projection_pages(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    source = workspace(
        seeded.home,
        tmp_path / "seed",
        user=seeded.user,
        proj=seeded.proj,
        session=seeded.sessions[-1],
        project_name=PROJECT_NAME,
    )
    store = FileStore(source.home)
    # More than the wire's 500-row page, with the last turn holding the
    # unique answer. Projection page one must widen past 500 to keep turn
    # one whole; its continuation must still reach the final turn.
    for n in range(502):
        await store.write("user:paging", f"docs/item-{n:04}", f"item {n}")
        await store.append_turn(
            "paged", [Message(role=Role.USER, content=f"paged turn {n + 1}")]
        )
    await store.append_projections(
        "paged",
        tuple(
            ConversationProjection(turn=1, kind="log", text=f"projection {n}", span=1)
            for n in range(501)
        )
        + (
            ConversationProjection(
                turn=502, kind="log", text="last projection", span=1
            ),
        ),
    )
    initialize = REMOTE_CASES[-1].rpc[:2]
    case = Case(
        "remote pages",
        ("mcp", "--root", "{home}", "--scope", "user:paging"),
        rpc=(
            *initialize,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "memory",
                    "arguments": {"command": "view", "path": "/memories"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "recall_turn",
                    "arguments": {"conversation": "paged", "turn": 502},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "continue_session",
                    "arguments": {"conversation": "paged"},
                },
            },
        ),
    )
    outputs: list[Any] = []
    for server in (python_door, rust_door):
        for client in (python_door, rust_door):
            space = workspace(
                source.home,
                tmp_path / str(len(outputs)),
                user=seeded.user,
                proj=seeded.proj,
                session=seeded.sessions[-1],
                project_name=PROJECT_NAME,
            )
            with serving(server, space) as url:
                remote = remote_case(case, url)
                result = run_case(client, remote, space)
                assert result.code == 0, result.stderr
                assert (
                    len(result.rpc[1]["result"]["content"][0]["text"].splitlines())
                    == 502
                )
                assert "paged turn 502" in result.rpc[2]["result"]["content"][0]["text"]
                outputs.append(shape_of_output(remote, result, space, keep=seeded.ids))
    assert outputs == [outputs[0]] * 4
