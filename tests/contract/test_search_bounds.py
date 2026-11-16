"""The relevance candidate bound on every door (S13, DESIGN §40.45).

`NEOSIAN_SEARCH_MAX_CANDIDATES` bounds how many candidates one relevance search
keeps facts for. Over it, history and memory relevance refuse with
`value_error` and a fixed message; the agent doors add guidance from the
shipped assets. Newest and path orders hold at most `limit` keys and never
refuse. At or under the bound the answers are the unbounded ones, byte for
byte. Expected texts are literal; the binary never produced them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.profile_support import run
from tests.contract.support import ADMIN, Binary, serve
from tests.contract.test_history_ranking import (
    CORPUS,
    QUERY,
    RANKED,
    listed,
    mcp,
    plant,
    refusal,
    scored,
    search,
)

BOUND = "NEOSIAN_SEARCH_MAX_CANDIDATES"
TURNS = (
    "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than 3 turns "
    "hold a search term; narrow the words or the time range"
)
DOCUMENTS = (
    "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than 2 documents "
    "hold a search term; narrow the words, the path or the update dates"
)
HISTORY_CLI = (
    "Too many turns hold one of these terms for this server to rank them. "
    "Search again with rarer, more specific terms (drop common words and single "
    "letters), limit the time with --since and --before, or name a "
    "--conversation. --order newest, which needs every term, is not limited "
    "this way."
)
HISTORY_MCP = (
    "Too many turns hold one of these terms for this server to rank them. "
    "Search again with rarer, more specific terms (drop common words and single "
    "letters), limit the time with since and before, or pass "
    "conversation=<id>. order=newest, which needs every term, is not limited "
    "this way."
)
MEMORY_CLI = (
    "Too many documents hold one of these terms for this server to rank them. "
    "Search again with rarer, more specific terms (drop common words and single "
    "letters), narrow --path to a folder, or limit --updated-since and "
    "--updated-before. --order newest and --order path, which need every term, "
    "are not limited this way."
)
MEMORY_MCP = (
    "Too many documents hold one of these terms for this server to rank them. "
    "Search again with rarer, more specific terms (drop common words and single "
    "letters), narrow path to a folder, or limit updated_since and "
    "updated_before. order=newest and order=path, which need every term, are "
    "not limited this way."
)


def test_history_relevance_refuses_past_the_bound_on_the_cli(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    # QUERY's candidates are four turns; the bound counts them.
    assert scored(search(binary, *QUERY, env={BOUND: "4"})) == RANKED
    assert scored(search(binary, *QUERY)) == RANKED
    human = run(binary, "search", *QUERY, env={BOUND: "3"})
    assert (human.returncode, human.stdout) == (1, "")
    assert human.stderr == f"error: {TURNS}\nhint: {HISTORY_CLI}\n"
    machine = run(binary, "search", *QUERY, "--json", env={BOUND: "3"})
    assert machine.returncode == 1
    assert json.loads(machine.stdout) == {"error": TURNS, "hint": HISTORY_CLI}
    # Newest holds at most `limit` keys, so the bound never applies to it.
    dashboard = [("beta-session", 2), ("alpha-session", 3), ("alpha-session", 1)]
    newest = search(binary, "dashboard", "--order", "newest", env={BOUND: "1"})
    assert listed(newest) == dashboard
    bounded = ("--order", "newest", "--since", "2026-11-01T00:00:00Z")
    assert listed(search(binary, "dashboard", *bounded, env={BOUND: "1"})) == dashboard
    # Narrowing the selection narrows the candidates.
    narrowed = ("--conversation", "beta-session")
    assert len(scored(search(binary, *QUERY, *narrowed, env={BOUND: "2"}))) == 2


@pytest.mark.parametrize("value", ["0", "-1", "ten", "10000001"])
def test_an_invalid_bound_is_refused_by_name(binary: Binary, value: str) -> None:
    plant(binary.home, CORPUS)
    refused = run(binary, "search", *QUERY, env={BOUND: value})
    assert refused.returncode == 1
    assert refused.stderr == (
        "error: NEOSIAN_SEARCH_MAX_CANDIDATES must be an integer in 1..10000000\n"
    )
    # Only relevance keeps facts, so only relevance reads the setting.
    assert run(binary, "search", "dashboard", "--order", "newest", env={BOUND: value}).returncode == 0  # fmt: skip


def test_mcp_search_history_names_the_bound_and_guides(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    _, results = mcp(
        binary,
        [
            {"query": "Dashboard RESPONSE time"},
            {"query": "dashboard", "order": "newest"},
        ],
        env={BOUND: "3"},
    )
    text = refusal(results[0])
    assert text == f"Tool 'search_history' failed: {TURNS}\n{HISTORY_MCP}"
    assert results[1].get("isError") is not True


def test_the_state_process_refuses_as_value_error(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    with serve(binary, extra_env={BOUND: "3"}) as server:
        body = {"query": "Dashboard RESPONSE time", "order": "relevance"}
        reply = server.request("/v1/conversation/search_turns", body)
        assert (reply.status, reply.body) == (
            400,
            {"error": {"code": "value_error", "message": TURNS, "details": {}}},
        )
        newest = server.request(
            "/v1/conversation/search_turns", {"query": "dashboard", "order": "newest"}
        )
        assert newest.status == 200 and len(newest.body["turns"]) == 3
        # A client presents the server's refusal with the same guidance.
        remote = run(
            binary,
            "search",
            *QUERY,
            "--url",
            server.url,
            env={"NEOSIAN_CLIENT_TOKEN": ADMIN},
        )
        assert remote.returncode == 1
        assert remote.stderr == f"error: {TURNS}\nhint: {HISTORY_CLI}\n"


def memory(binary: Binary) -> None:
    for name, content in [
        ("alpha", "deploy the dashboard"),
        ("beta", "deploy notes"),
        ("gamma", "deploy again"),
    ]:
        created = run(
            binary,
            "memory",
            "create",
            f"/memories/{name}",
            "--scope",
            "user:bounds",
            "--content",
            content,
        )
        assert created.returncode == 0, created.stderr


def memory_search(binary: Binary, *args: str, env: dict[str, str]) -> Any:
    found = run(
        binary, "memory", "search", *args, "--scope", "user:bounds", "--json", env=env
    )
    return found


def test_memory_relevance_refuses_past_the_bound(binary: Binary) -> None:
    memory(binary)
    within = memory_search(binary, "deploy", env={BOUND: "3"})
    unbounded = memory_search(binary, "deploy", env={})
    assert within.returncode == 0 and within.stdout == unbounded.stdout
    refused = memory_search(binary, "deploy", env={BOUND: "2"})
    assert refused.returncode == 1
    assert json.loads(refused.stdout) == {"error": DOCUMENTS, "hint": MEMORY_CLI}
    assert refused.stderr == f"error: {DOCUMENTS}\nhint: {MEMORY_CLI}\n"
    for order in ("newest", "path"):
        page = memory_search(binary, "deploy", "--order", order, env={BOUND: "1"})
        assert page.returncode == 0, page.stderr
        assert len(json.loads(page.stdout)["hits"]) == 3


def test_mcp_and_http_memory_search_refuse_past_the_bound(binary: Binary) -> None:
    memory(binary)
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
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_memory", "arguments": {"query": "deploy"}},
        },
    ]
    process = run(
        binary,
        "mcp",
        "--scope",
        "user:bounds",
        env={BOUND: "2"},
        stdin="".join(json.dumps(r) + "\n" for r in requests),
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.splitlines()[1])["result"]
    assert refusal(result) == f"Tool 'search_memory' failed: {DOCUMENTS}\n{MEMORY_MCP}"
    with serve(binary, extra_env={BOUND: "2"}) as server:
        body = {"scope": "user:bounds", "query": "deploy", "order": "relevance"}
        reply = server.request("/v1/memory/search", body)
        assert (reply.status, reply.body) == (
            400,
            {"error": {"code": "value_error", "message": DOCUMENTS, "details": {}}},
        )
        body["order"] = "newest"
        assert len(server.request("/v1/memory/search", body).body["hits"]) == 3
