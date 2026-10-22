"""The record and MCP doors against both implementations of the wire."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian import RemoteStore
from tests.differential.corpus import IDS, SESSION
from tests.differential.harness import (
    TOKEN,
    Case,
    Workspace,
    free_port,
    run_case,
    shape_of_output,
    snapshot,
    workspace,
)
from tests.differential.remote_cases import REMOTE_CASES, SPAN, remote_case, serving
from tests.differential.seed import PROJECT_NAME, Seeded


def _space(seeded: Seeded, target: Path) -> Workspace:
    return workspace(
        seeded.home,
        target,
        user=seeded.user,
        proj=seeded.proj,
        session=seeded.sessions[-1],
        project_name=PROJECT_NAME,
    )


@pytest.mark.parametrize("case", REMOTE_CASES, ids=[case.name for case in REMOTE_CASES])
def test_remote_corpus_cross_pairs(
    case: Case, seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    observed: list[Any] = []
    keep = seeded.ids | IDS
    for server in (python_door, rust_door):
        for client in (python_door, rust_door):
            space = _space(seeded, tmp_path / str(len(observed)))
            with serving(server, space) as url:
                remote = remote_case(case, url)
                outcome = run_case(client, remote, space)
            observed.append(
                (
                    shape_of_output(remote, outcome, space, keep=keep),
                    snapshot(space, keep=keep),
                )
            )
    assert observed == [observed[0]] * 4


async def test_recorded_span_is_read_by_python_from_every_pair(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    observed: list[Any] = []
    keep = seeded.ids | IDS
    for server in (python_door, rust_door):
        for client in (python_door, rust_door):
            space = _space(seeded, tmp_path / str(len(observed)))
            outputs = []
            with serving(server, space) as url:
                for case in SPAN:
                    remote = remote_case(case, url)
                    outcome = run_case(client, remote, space)
                    assert outcome.code == 0, outcome.stderr
                    outputs.append(shape_of_output(remote, outcome, space, keep=keep))
                async with await RemoteStore.connect(url, token=TOKEN) as store:
                    turns = await store.read_turns(SESSION)
                    assert len(turns) == 1
                    assert turns[0].actor == f"client:default/claude-code:{SESSION}"
                    assert turns[0].messages[0].content == "Add a test for login"
                    document = await store.read(space.proj, f"sessions/{SESSION}")
                    assert document is not None
                    assert document.actor == f"client:default/claude-code:{SESSION}#1"
                assert not (space.spool / f"{SESSION}.jsonl").exists()
            observed.append((outputs, snapshot(space, keep=keep)))
    assert observed == [observed[0]] * 4


@pytest.mark.parametrize("server_kind", ["python", "rust"])
def test_unavailable_server_keeps_the_spool_until_landing(
    server_kind: str, seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    space = _space(seeded, tmp_path / server_kind)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    prompt = run_case(rust_door, remote_case(SPAN[0], url), space)
    assert prompt.code == 0  # mailbox is best-effort; the prompt is already safe
    stopped = run_case(rust_door, remote_case(SPAN[2], url), space)
    assert stopped.code == 1
    spool = space.spool / f"{SESSION}.jsonl"
    rows = [json.loads(line) for line in spool.read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["prompt", "stop"]
    server = python_door if server_kind == "python" else rust_door
    with serving(server, space, port=port):
        landed = run_case(rust_door, remote_case(SPAN[2], url), space)
        assert landed.code == 0, landed.stderr
        assert json.loads(landed.stdout)["disposition"] == "recorded"
        assert not spool.exists()
        response = httpx.post(
            f"{url}/v1/conversation/read_turns",
            json={"conversation_id": SESSION},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert len(response.json()["turns"]) == 1


async def test_mailbox_ack_is_versioned_once_across_every_pair(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    from neosian import FileStore
    from tests.differential.corpus import MOUNTS

    source = FileStore(seeded.home)
    entries = await source.list_documents(seeded.proj, prefix="messages/")
    assert len(entries) == 1
    message_id = entries[0].path.removeprefix("messages/")
    calls = tuple(
        {
            "jsonrpc": "2.0",
            "id": n,
            "method": "tools/call",
            "params": {
                "name": "messages",
                "arguments": {
                    "command": "ack",
                    "scope": "/project",
                    "message_id": message_id,
                    "session": SESSION,
                    "occurrence": 1,
                },
            },
        }
        for n in (2, 3)
    )
    case = Case(
        "remote mailbox ack", ("mcp", *MOUNTS), rpc=(*REMOTE_CASES[-1].rpc[:2], *calls)
    )
    observed: list[Any] = []
    for server in (python_door, rust_door):
        for client in (python_door, rust_door):
            space = _space(seeded, tmp_path / str(len(observed)))
            with serving(server, space) as url:
                remote = remote_case(case, url)
                result = run_case(client, remote, space)
                assert result.code == 0, result.stderr
                assert all(not row["result"]["isError"] for row in result.rpc[1:])
                async with await RemoteStore.connect(url, token=TOKEN) as store:
                    rows = await store.versions(space.proj, entries[0].path)
                    assert len(rows) == 2
                    assert rows[0].actor == "client:default/mcp:stdio"
                    assert json.loads(rows[0].content)["read_by"] == SESSION
                observed.append(
                    (
                        shape_of_output(remote, result, space, keep=seeded.ids | IDS),
                        snapshot(space, keep=seeded.ids | IDS),
                    )
                )
    assert observed == [observed[0]] * 4
