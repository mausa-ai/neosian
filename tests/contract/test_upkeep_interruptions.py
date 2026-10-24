"""Faults leave inspectable revisions, not a promise of batch rollback or replay."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.knowledge_support import success, unpack
from tests.contract.support import Binary, serve
from tests.contract.test_transport import TOKEN, responder
from tests.contract.upkeep_support import edit, invoke, prepare


@pytest.mark.parametrize(
    "failure", ["second_write", "lost_acknowledgment", "concurrent_edit"]
)
def test_interrupted_batches_and_version_conflicts_preserve_ordinary_history(
    binary: Binary, failure: str
) -> None:
    with serve(binary) as server:
        for name in ["notes/a", "notes/b"]:
            server.post(
                "memory/write",
                scope="user:upkeep",
                path=name,
                content="Original " + name,
            )
        armed = False
        writes: list[str] = []

        def respond(route: str, body: dict[str, Any]) -> tuple[int, Any]:
            nonlocal armed
            if route == "/v1/memory/write" and armed:
                path = str(body["path"])
                writes.append(path)
                assert body["expected_version"] == 1
                if failure == "concurrent_edit":
                    server.post(
                        "memory/write",
                        scope="user:upkeep",
                        path=path,
                        content="Concurrent human edit",
                    )
                    armed = False
                if (
                    failure == "second_write" and path == "notes/b"
                ) or failure == "lost_acknowledgment":
                    if failure == "lost_acknowledgment":
                        committed = server.request(route, body)
                        assert committed.status == 200
                    armed = False
                    return 500, {
                        "error": {
                            "code": "agent_configuration_error",
                            "message": "injected connection interruption",
                            "details": {},
                        }
                    }
            response = server.request(
                route, body, method="GET" if route == "/v1/capabilities" else "POST"
            )
            return response.status, response.body

        with responder(respond) as (url, _):
            flags = ["--scope", "user:upkeep", "--url", url]
            env = {"NEOSIAN_CLIENT_TOKEN": TOKEN}
            path, proposal = prepare(
                binary, [edit(), edit("/memories/notes/b")], flags, env
            )
            armed = True
            stopped = invoke(
                binary,
                "apply",
                str(path),
                "--write",
                "--approve",
                proposal["id"],
                flags=flags,
                env=env,
            )
            assert stopped.returncode == 1
            report = json.loads(stopped.stdout)
            assert not report["success"]
            assert len(report["applied"]) == (1 if failure == "second_write" else 0)
            assert writes == (
                ["notes/a", "notes/b"] if failure == "second_write" else ["notes/a"]
            )
            inspected = success(
                invoke(binary, "inspect", str(path), flags=flags, env=env)
            )
            state = next(
                row["state"]
                for row in inspected["observations"]
                if row["path"] == "/memories/notes/a"
            )
            assert state == (
                "changed"
                if failure == "concurrent_edit"
                else "matches_proposed_content"
            )
            before = len(writes)
            assert (
                invoke(
                    binary,
                    "apply",
                    str(path),
                    "--write",
                    "--approve",
                    proposal["id"],
                    flags=flags,
                    env=env,
                ).returncode
                == 1
            )
            assert len(writes) == before
            assert (
                server.post("memory/read", scope="user:upkeep", path="notes/b")[
                    "document"
                ]["version"]
                == 1
            )
            history = server.post(
                "memory/versions", scope="user:upkeep", path="notes/a"
            )["versions"]
            assert [r["version"] for r in history] == [2, 1]
            assert history[1]["content"] == "Original notes/a"
            path, proposal = prepare(binary, [edit("/memories/notes/b")], flags, env)
            success(
                invoke(
                    binary,
                    "apply",
                    str(path),
                    "--write",
                    "--approve",
                    proposal["id"],
                    flags=flags,
                    env=env,
                )
            )
            assert (
                unpack(
                    server.post("memory/read", scope="user:upkeep", path="notes/b")[
                        "document"
                    ]["content"]
                )[1]
                == "Corrected café.\n"
            )
