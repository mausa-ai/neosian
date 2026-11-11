"""Matched passages through the agent doors (CLI, MCP) and remote stores.

Offsets come from an independent Python reference of DESIGN §40.42.
"""

from __future__ import annotations

import json
from typing import Any

from tests.contract.profile_support import run
from tests.contract.support import Binary
from tests.contract.test_memory_ranking import mcp, search, text
from tests.contract.test_search_authority import hit
from tests.contract.test_transport import TOKEN, responder

FILLER = "word " * 60
GUIDE = (
    f"# Guide\n{FILLER}deploy once.\n## Release\n"
    f"İ {FILLER}deploy then review the deploy.\n{FILLER}review\n"
)
CAPABLE = {
    "wire_version": 6,
    "memory_search": {"version": 1},
    "memory_ranking": {"version": 1},
}


def test_agent_doors_return_three_passages_by_default(binary: Binary) -> None:
    binary.json(
        "memory", "create", "/memories/guide", "--content", GUIDE,
        "--scope", "user:demo",
    )  # fmt: skip
    page = search(binary, "deploy review", "--scope", "user:demo")
    found = [(p["offset"], p["heading"]) for p in page["hits"][0]["passages"]]
    assert found == [(268, "Guide"), (594, "Release"), (925, "Release")]
    none = search(binary, "deploy review", "--scope", "user:demo", "--passages", "0")
    assert "passages" not in none["hits"][0]
    human = binary.run("memory", "search", "deploy review", "--scope", "user:demo")
    assert "  passage 594 (Release): " in human.stdout
    answers = mcp(
        binary,
        [
            ("search_memory", {"query": "deploy review"}),
            ("search_memory", {"query": "deploy review", "passages": 1}),
            ("search_memory", {"query": "deploy review", "passages": 4}),
        ],
    )
    schema = {t["name"]: t for t in answers[1]["result"]["tools"]}["search_memory"]
    assert "passages" in schema["inputSchema"]["properties"]
    assert len(text(answers[2])["hits"][0]["passages"]) == 3
    assert [p["offset"] for p in text(answers[3])["hits"][0]["passages"]] == [594]
    assert answers[4]["result"]["isError"] is True


def test_services_without_passages_omit_them_only_by_default(binary: Binary) -> None:
    observed: list[dict[str, Any]] = []

    def reply(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, CAPABLE
        observed.append(body)
        return 200, {
            "hits": [hit(score=7)],
            "next_cursor": None,
            "order": "relevance",
        }

    with responder(reply) as (url, requests):
        flags = ["--url", url, "--scope", "user:test"]
        page = search(binary, "needle", *flags, NEOSIAN_CLIENT_TOKEN=TOKEN)
        assert "memory-passages" in page["fallback"]
        assert page["order"] == "relevance" and "passages" not in observed[0]
        requests.clear()
        result = run(
            binary,
            "memory",
            "search",
            "needle",
            *flags,
            "--passages",
            "2",
            "--json",
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert result.returncode == 1
        assert "does not support memory-passages" in result.stderr
        assert "/v1/memory/search" not in requests


def test_malformed_passages_are_refused(binary: Binary) -> None:
    window = {"field": "content", "metadata_key": None, "heading": None}
    pages: list[list[dict[str, Any]] | None] = [
        None,
        [window | {"offset": 0, "text": "x"}] * 4,
        [
            window | {"offset": 10, "text": "x" * 20},
            window | {"offset": 15, "text": "y"},
        ],
        [window | {"offset": 0, "text": ""}],
        [window | {"offset": 0, "text": "x", "field": "path"}],
        [window | {"offset": 0, "text": "x", "heading": ""}],
    ]
    for passages in pages:
        hit_body = hit(score=7) | ({} if passages is None else {"passages": passages})

        def reply(
            path: str, _body: dict[str, Any], page: Any = hit_body
        ) -> tuple[int, Any]:
            if path == "/v1/capabilities":
                return 200, CAPABLE | {"memory_passages": {"version": 1}}
            return 200, {"hits": [page], "next_cursor": None, "order": "relevance"}

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
            assert result.returncode == 1, passages
            assert "malformed memory-search" in result.stderr
    good = hit(score=7) | {"passages": [window | {"offset": 0, "text": "x"}]}

    def accept(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, CAPABLE | {"memory_passages": {"version": 1}}
        return 200, {"hits": [good], "next_cursor": None, "order": "relevance"}

    with responder(accept) as (url, _requests):
        page = search(
            binary, "needle", "--url", url, "--scope", "user:test",
            NEOSIAN_CLIENT_TOKEN=TOKEN,
        )  # fmt: skip
        assert page["hits"][0]["passages"] == good["passages"]
        assert json.dumps(page).count("passages") == 1
