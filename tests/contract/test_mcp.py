"""A raw JSON-RPC client pins retained tools without an MCP or product SDK."""

from __future__ import annotations

import json
import shutil
from typing import Any

from tests.contract.support import FIXTURES, Binary, package_version


def test_stdio_tools_history_and_refusals(binary: Binary) -> None:
    shutil.copytree(FIXTURES / "home", binary.home)
    requests: list[dict[str, Any]] = [
        {"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "contract", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    calls = [
        ("memory", {"command": "view", "path": "/memories/notes/fact"}),
        ("memory", {"command": "create", "path": "/memories/new", "content": "é"}),
        ("memory", {"command": "view", "path": "/memories/new"}),
        ("recall_turn", {"turn": 1, "conversation": "contract-session"}),
        ("search_history", {"query": "CAFÉ memory"}),
        ("recall_turn", {"turn": "bad"}),
        ("messages", {"command": "list"}),
        ("missing", {}),
        ("docs", {}),
        ("docs", {"topic": "memory"}),
        ("memory_history", {"path": "/memories/notes/fact", "limit": 1}),
        ("memory_history", {"path": "/elsewhere/secret"}),
        ("memory_history", {"path": "/memories", "limit": 101}),
        ("load_skill", {"name": "upkeep"}),
    ]
    for number, (name, arguments) in enumerate(calls, 3):
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
        "user:contract",
        "--actor",
        "mcp:contract",
        stdin="".join(json.dumps(request) + "\n" for request in requests),
    )
    assert (process.returncode, process.stderr) == (0, "")
    answers = [json.loads(line) for line in process.stdout.splitlines()]
    assert [a["id"] for a in answers] == list(range(17))
    assert all(a["jsonrpc"] == "2.0" for a in answers)
    assert answers[0]["error"]["code"] == -32602
    initialized = answers[1]["result"]
    assert initialized["protocolVersion"] == "2025-06-18"
    assert initialized["serverInfo"] == {
        "name": "neosian-memory",
        "version": package_version(),
    }
    assert "/memories/notes/fact" in initialized["instructions"]
    assert [tool["name"] for tool in answers[2]["result"]["tools"]] == [
        "memory",
        "list_skills",
        "load_skill",
        "messages",
        "recall_turn",
        "search_history",
        "continue_session",
        "handoff",
        "docs",
        "memory_history",
        "search_memory",
        "upkeep_review",
        "upkeep_apply",
    ]

    def text(number: int) -> str:
        return str(answers[number]["result"]["content"][0]["text"])

    assert text(3) == "/memories/notes/fact (v2):\n1: café ✓\n2: "
    assert text(4) == "Created /memories/new (v1)"
    assert text(5) == "/memories/new (v1):\n1: é"
    assert text(6).startswith("Turn 1 (verbatim):\nUSER: Review café memory\n")
    assert "contract-session" in text(7)
    assert answers[8]["result"]["isError"] is True
    assert text(8) == (
        "Invalid arguments for tool 'recall_turn': turn: Input should be a valid integer, "
        "unable to parse string as an integer; conversation: Field required"
    )
    assert json.loads(text(9))["items"][0]["body"] == "Review the saved decision."
    assert answers[10]["result"]["isError"] is True
    assert text(10) == "Tool 'missing' not found"
    assert "memory" in {row["topic"] for row in json.loads(text(11))}
    assert "memory" in text(12).lower()
    rows = json.loads(text(13))["entries"]
    assert len(rows) == 1 and rows[0]["version"] == 2
    assert "content" not in rows[0]
    assert answers[14]["result"]["isError"] is True
    assert answers[15]["result"]["isError"] is True
    assert "pause other writers" in text(16).lower()
    tools = {tool["name"]: tool for tool in answers[2]["result"]["tools"]}
    for name in ("docs", "memory_history", "search_memory", "upkeep_review"):
        tool = tools[name]
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
