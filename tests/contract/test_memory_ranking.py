"""Ranked memory search through the agent doors (CLI, MCP) and remote stores.

Scores are literal values from an independent Python reference of the pinned
formula (DESIGN §40.41); the binary under test never produced them.
"""

from __future__ import annotations

import json
from typing import Any

from tests.contract.profile_support import run
from tests.contract.support import ADMIN, Binary, serve
from tests.contract.test_search_authority import hit
from tests.contract.test_transport import TOKEN, responder

DEPLOY = {
    "notes/deploy": "Deploy window is Friday. Deploy after review.",
    "notes/review": "Review the deploy checklist.",
    "notes/lunch": "Lunch is at noon.",
    "skills/release": "Release steps: tag, deploy, announce.",
}
SCORES = [
    ("notes/review", 1348537),
    ("notes/deploy", 1145519),
    ("skills/release", 334880),
]
MOUNTS = ["--mount", "scope=user:a,path=alpha", "--mount", "scope=user:b,path=beta"]


def search(binary: Binary, *args: str, **env: str) -> Any:
    result = run(binary, "memory", "search", *args, "--json", env=env or None)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def mcp(binary: Binary, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "contract", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ]
    for number, (name, arguments) in enumerate(calls, 2):
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
    process = binary.run(
        "mcp",
        "--scope",
        "user:demo",
        "--actor",
        "mcp:contract",
        stdin="".join(json.dumps(r) + "\n" for r in requests),
    )
    assert process.returncode == 0, process.stderr
    return [json.loads(line) for line in process.stdout.splitlines()]


def text(answer: Any) -> Any:
    assert answer["result"].get("isError") is not True, answer
    return json.loads(answer["result"]["content"][0]["text"])


def test_cli_ranks_by_default_and_keeps_explicit_orders(binary: Binary) -> None:
    for path, content in DEPLOY.items():
        binary.json(
            "memory", "create", "/memories/" + path, "--content", content,
            "--scope", "user:demo",
        )  # fmt: skip
    page = search(binary, "deploy REVIEW", "--scope", "user:demo")
    assert page["order"] == "relevance" and page["next_cursor"] is None
    assert [(h["path"], h["score"]) for h in page["hits"]] == SCORES
    assert page["hits"][0]["reference"] == "/memories/notes/review"
    human = binary.run("memory", "search", "deploy REVIEW", "--scope", "user:demo")
    assert human.stdout.splitlines()[0] == "order: relevance"
    assert "score=1348537" in human.stdout.splitlines()[1]
    path = search(binary, "deploy", "--scope", "user:demo", "--order", "path")
    assert path["order"] == "path"
    assert [h["path"] for h in path["hits"]] == [
        "notes/deploy",
        "notes/review",
        "skills/release",
    ]
    assert all("score" not in h for h in path["hits"])
    newest = search(binary, "deploy", "--scope", "user:demo", "--order", "newest")
    assert [h["path"] for h in newest["hits"]] == [
        "skills/release",
        "notes/review",
        "notes/deploy",
    ]
    future = search(
        binary, "deploy", "--scope", "user:demo", "--updated-since",
        "2999-01-01T00:00:00Z",
    )  # fmt: skip
    assert future == {"hits": [], "next_cursor": None, "order": "relevance"}
    for refused in [
        ["--order", "relevance", "--cursor", "x"],
        ["--updated-before", "2026-11-11T10:00:00+01:00"],
    ]:
        result = binary.run(
            "memory", "search", "deploy", "--scope", "user:demo", *refused
        )
        assert result.returncode == 2 and result.stdout == ""


def test_mounts_merge_by_rank_and_by_time(binary: Binary) -> None:
    for path, content in [
        ("/alpha/a2", "needle among several other words"),
        ("/beta/b1", "needle"),
        ("/alpha/a1", "needle needle"),
    ]:
        binary.json("memory", "create", path, "--content", content, *MOUNTS)
    ranked = search(binary, "needle", *MOUNTS)
    assert [h["reference"] for h in ranked["hits"]] == [
        "/alpha/a1",
        "/beta/b1",
        "/alpha/a2",
    ]
    newest = search(binary, "needle", *MOUNTS, "--order", "newest")
    assert [h["reference"] for h in newest["hits"]] == [
        "/alpha/a1",
        "/beta/b1",
        "/alpha/a2",
    ]
    limited = search(binary, "needle", *MOUNTS, "--limit", "1")
    assert [h["reference"] for h in limited["hits"]] == ["/alpha/a1"]


def test_remote_ranking_matches_local_scores(binary: Binary) -> None:
    with serve(binary) as server:
        for path, content in DEPLOY.items():
            server.post(
                "memory/write", scope="workspace:portable", path=path, content=content
            )
        page = search(
            binary, "deploy REVIEW", "--url", server.url, "--mount",
            "scope=workspace:portable,path=project,ro",
            NEOSIAN_CLIENT_TOKEN=ADMIN,
        )  # fmt: skip
        assert page["order"] == "relevance" and "fallback" not in page
        assert [(h["path"], h["score"]) for h in page["hits"]] == SCORES


def test_mcp_ranks_by_default_and_continues_path_cursors(binary: Binary) -> None:
    calls: list[tuple[str, dict[str, Any]]] = [
        ("memory", {"command": "create", "path": "/memories/" + path, "content": body})
        for path, body in DEPLOY.items()
    ]
    calls += [
        ("search_memory", {"query": "deploy REVIEW"}),
        ("search_memory", {"query": "deploy", "order": "path", "limit": 1}),
    ]
    answers = mcp(binary, calls)
    schema = {t["name"]: t for t in answers[1]["result"]["tools"]}["search_memory"][
        "inputSchema"
    ]["properties"]
    assert schema["order"]["anyOf"][0]["enum"] == ["relevance", "newest", "path"]
    assert {"updated_since", "updated_before"} <= set(schema)
    ranked = text(answers[6])
    assert ranked["order"] == "relevance"
    assert [(h["path"], h["score"]) for h in ranked["hits"]] == SCORES
    first = text(answers[7])
    assert first["order"] == "path" and [h["path"] for h in first["hits"]] == [
        "notes/deploy"
    ]
    more = mcp(
        binary,
        [
            (
                "search_memory",
                {"query": "deploy", "limit": 1, "cursor": first["next_cursor"]},
            )
        ],
    )
    second = text(more[2])
    assert second["order"] == "path"
    assert [h["path"] for h in second["hits"]] == ["notes/review"]


def test_version_1_services_fall_back_only_for_the_implicit_order(
    binary: Binary,
) -> None:
    observed: list[dict[str, Any]] = []

    def reply(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "memory_search": {"version": 1}}
        observed.append(body)
        return 200, {"hits": [hit()], "next_cursor": None}

    with responder(reply) as (url, requests):
        flags = ["--url", url, "--scope", "user:test"]
        page = search(binary, "needle", *flags, NEOSIAN_CLIENT_TOKEN=TOKEN)
        assert page["order"] == "path"
        assert "memory-ranking" in page["fallback"]
        assert "order" not in observed[0] and "updated_since" not in observed[0]
        explicit = search(
            binary, "needle", *flags, "--order", "path", NEOSIAN_CLIENT_TOKEN=TOKEN
        )
        assert explicit["order"] == "path"
        # Only the passages default may still fall back here (S11).
        assert "memory-ranking" not in explicit.get("fallback", "")
        requests.clear()
        for refused in [["--order", "relevance"], ["--order", "newest"]] + [
            ["--updated-since", "2026-01-01T00:00:00Z"]
        ]:
            result = run(
                binary,
                "memory",
                "search",
                "needle",
                *flags,
                *refused,
                "--json",
                env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
            )
            assert result.returncode == 1
            assert "does not support memory-ranking" in result.stderr
        assert "/v1/memory/search" not in requests


def test_malformed_ranked_pages_are_refused(binary: Binary) -> None:
    pages = [
        {"hits": [hit()], "next_cursor": None, "order": "relevance"},
        {"hits": [hit(score=5)], "next_cursor": None},
        {
            "hits": [hit("a", score=1), hit("b", score=2)],
            "next_cursor": None,
            "order": "relevance",
        },
        {"hits": [hit(score=3)], "next_cursor": "more", "order": "relevance"},
        {"hits": [hit(score=3)], "next_cursor": None, "order": "path"},
    ]
    for page in pages:

        def reply(
            path: str, _body: dict[str, Any], page: Any = page
        ) -> tuple[int, Any]:
            if path == "/v1/capabilities":
                return 200, {
                    "wire_version": 6,
                    "memory_search": {"version": 1},
                    "memory_ranking": {"version": 1},
                }
            return 200, page

        with responder(reply) as (url, _requests):
            result = run(
                binary,
                "memory",
                "search",
                "needle",
                "--url",
                url,
                "--scope",
                "user:test",
                "--json",
                env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
            )
            assert result.returncode == 1, page
            assert "malformed memory-search" in result.stderr
