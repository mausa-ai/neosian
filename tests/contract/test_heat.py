"""Agent reads leave one heat row per session and document; nothing else does."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from tests.contract.support import FIXTURES, Binary

SCOPE = "user:contract"
SCOPE_FILE = "user%3Acontract.jsonl"
MAILBOX = "/memories/messages/00000000-0000-4000-8000-000000000001"
MOUNTS = (
    "--mount",
    f"scope={SCOPE},path=memories",
    "--mount",
    "scope=user:other,path=other",
)


def rpc(
    binary: Binary,
    calls: list[tuple[str, dict[str, Any]]],
    env: dict[str, str] | None = None,
    store: tuple[str, ...] = ("--scope", SCOPE),
) -> list[dict[str, Any]]:
    """One stdio MCP session: initialize, then every call; its answers in order."""
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
    ]
    for number, (name, arguments) in enumerate(calls, 1):
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
    process = subprocess.run(
        [str(binary.executable), "mcp", *store, "--actor", "mcp:contract"],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**binary.environment(), **(env or {})},
        cwd=binary.directory,
        timeout=30,
        check=False,
    )
    assert (process.returncode, process.stderr) == (0, "")
    answers = [json.loads(line) for line in process.stdout.splitlines()]
    return answers[1:]


def text(answer: dict[str, Any]) -> str:
    return str(answer["result"]["content"][0]["text"])


def rows(binary: Binary) -> list[dict[str, Any]]:
    file = binary.home / "heat" / SCOPE_FILE
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]


def view(path: str) -> tuple[str, dict[str, Any]]:
    return ("memory", {"command": "view", "path": path})


def test_only_delivered_agent_reads_are_logged_once_per_session(
    binary: Binary,
) -> None:
    shutil.copytree(FIXTURES / "home", binary.home)
    answers = rpc(
        binary,
        [
            view("/"),
            view("/memories/notes"),
            view("/memories/notes/fact"),
            (
                "memory",
                {
                    "command": "view",
                    "path": "/memories/notes/fact",
                    "view_range": [1, 1],
                },
            ),
            view("/memories/missing"),
            ("memory", {"command": "create", "path": "/memories/new", "content": "é"}),
            view("/memories/new"),
            (
                "knowledge",
                {"command": "read", "path": "/memories/notes/fact", "policy": "all"},
            ),
            (
                "knowledge",
                {"command": "read", "path": "/memories/new", "policy": "approved"},
            ),
            view(MAILBOX),
        ],
    )
    assert answers[4]["result"]["isError"] is True
    assert text(answers[9]).startswith(MAILBOX + " (v")
    assert json.loads(text(answers[7]))["included"] is True
    assert json.loads(text(answers[8]))["included"] is False
    logged = rows(binary)
    assert [row["path"] for row in logged] == ["notes/fact", "new"]
    assert len({row["reader"] for row in logged}) == 1
    assert set(logged[0]) == {"at", "reader", "path"} and logged[0]["at"].endswith("Z")
    # A second session is a second reader; the shell is none; the switch holds the log.
    rpc(binary, [view("/memories/notes/fact")])
    for path in ("/memories/notes/fact", "/memories/new"):
        assert binary.run("memory", "view", path, "--scope", SCOPE).returncode == 0
    rpc(binary, [view("/memories/new")], env={"NEOSIAN_HEAT": "off"})
    logged = rows(binary)
    assert [row["path"] for row in logged] == ["notes/fact", "new", "notes/fact"]
    assert len({row["reader"] for row in logged}) == 2


def test_heat_report_orders_read_documents_and_scrubs_with_the_document(
    binary: Binary,
) -> None:
    shutil.copytree(FIXTURES / "home", binary.home)
    for name, content in (("a", "a"), ("b", "b")):
        created = binary.run(
            "memory",
            "create",
            f"/memories/notes/{name}",
            "--content",
            content,
            "--scope",
            SCOPE,
        )
        assert created.returncode == 0, created.stderr
    rpc(binary, [view("/memories/notes/a"), view("/memories/notes/fact")])
    rpc(binary, [view("/memories/notes/a")])
    report = binary.json("memory", "heat", "/memories", "--scope", SCOPE)
    assert (report["enabled"], report["read"], report["unread"]) == (True, 2, 1)
    assert not report["truncated"]
    assert [
        (d["path"], d["scope"], d["sessions_7d"], d["sessions_30d"], d["sessions"])
        for d in report["documents"]
    ] == [
        ("/memories/notes/a", SCOPE, 2, 2, 2),
        ("/memories/notes/fact", SCOPE, 1, 1, 1),
    ]
    assert all(d["last_read"].endswith("Z") for d in report["documents"])
    shown = binary.run(
        "memory", "heat", "/memories/notes", "--scope", SCOPE, "--limit", "1"
    )
    lines = shown.stdout.splitlines()
    assert lines[0].startswith("heat under /memories/notes: sessions that read")
    assert lines[1].startswith("- /memories/notes/a  2 / 2 / 2  20")
    assert lines[2:] == ["(1 more read; raise --limit)", "1 document never read"]
    # A rename carries the rows, a delete and a redaction drop them.
    for verb in (
        ("rename", "/memories/notes/a", "/memories/notes/c"),
        ("delete", "/memories/notes/fact"),
    ):
        assert binary.run("memory", *verb, "--scope", SCOPE).returncode == 0
    assert [row["path"] for row in rows(binary)] == ["notes/c", "notes/c"]
    report = binary.json("memory", "heat", "/", "--scope", SCOPE)
    assert [d["path"] for d in report["documents"]] == ["/memories/notes/c"]
    # Upkeep findings carry the evidence: d duplicates c and one session read d.
    created = binary.run(
        "memory", "create", "/memories/notes/d", "--content", "a", "--scope", SCOPE
    )
    assert created.returncode == 0, created.stderr
    rpc(binary, [view("/memories/notes/d")])
    scanned = binary.json("upkeep", "scan", "/memories/notes", "--scope", SCOPE)
    finding = {row["path"]: row for row in scanned["findings"]}["/memories/notes/d"]
    assert finding["issues"][0]["kind"] == "duplicate"
    assert (
        finding["read_sessions_7d"],
        finding["read_sessions_30d"],
        finding["read_sessions"],
    ) == (1, 1, 1)
    assert finding["last_read"].endswith("Z")
    assert (
        binary.run("memory", "redact", "/memories/notes/c", "--scope", SCOPE).returncode
        == 0
    )
    assert [row["path"] for row in rows(binary)] == ["notes/d"]
    off = subprocess.run(
        [str(binary.executable), "memory", "heat", "--scope", SCOPE],
        capture_output=True,
        text=True,
        env={**binary.environment(), "NEOSIAN_HEAT": "off"},
        cwd=binary.directory,
        timeout=30,
        check=False,
    )
    assert off.stdout.splitlines()[-1] == "(heat log off: NEOSIAN_HEAT=off)"


def test_related_lists_what_the_same_sessions_read_across_mounts(
    binary: Binary,
) -> None:
    shutil.copytree(FIXTURES / "home", binary.home)
    a, b, d, c = (
        "/memories/notes/a",
        "/memories/notes/b",
        "/memories/notes/d",
        "/other/c",
    )
    for path in (a, b, d, c):
        created = binary.run("memory", "create", path, "--content", path, *MOUNTS)
        assert created.returncode == 0, created.stderr
    for session in ((a, b, c, d), (a, b), (c, b)):
        rpc(binary, [view(path) for path in session], store=MOUNTS)
    report = binary.json("memory", "heat", a, "--related", *MOUNTS)
    assert (report["enabled"], report["sessions"], report["observed"]) == (True, 2, 3)
    assert not report["truncated"]
    # Most shared first; on a tie the narrower document (fewer readers) leads.
    assert [
        (r["path"], r["scope"], r["shared"], r["sessions"]) for r in report["related"]
    ] == [(b, SCOPE, 2, 3), (d, SCOPE, 1, 1), (c, "user:other", 1, 2)]
    other = binary.json("memory", "heat", c, "--related", *MOUNTS)
    assert [r["path"] for r in other["related"]] == [b, d, a]
    shown = binary.run("memory", "heat", a, "--related", "--limit", "1", *MOUNTS)
    assert shown.stdout.splitlines() == [
        f"related to {a}: read by 2 of 3 logged sessions; "
        "what they also read (shared / read by)",
        f"- {b}  2 / 3",
        "(2 more related; raise --limit)",
    ]
    lonely = binary.run("memory", "heat", "/memories/notes/fact", "--related", *MOUNTS)
    assert lonely.stdout.splitlines()[-1] == "no session has read it"
    root = binary.run("memory", "heat", "/memories", "--related", *MOUNTS)
    assert root.returncode == 2 and "--related needs one document" in root.stderr
    missing = binary.run("memory", "heat", "/memories/notes/x", "--related", *MOUNTS)
    assert missing.returncode == 1 and "memory_document_not_found" in missing.stderr
