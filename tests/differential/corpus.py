"""The corpus (DESIGN §39.4): every case the replay holds against both
doors over the seeded home. The hook payloads per client are the
documented shapes the unit tier replays (the same builders); the memory
grammar, the operator verbs and one MCP session follow. A case asserts
only once its verb is in `harness.BINARY_VERBS`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from tests.differential.harness import Case
from tests.unit.record.payloads import prompt, session_start, stop, tool

# The corpus's own sessions: new conversations beside the seed's.
SESSION: Final = "7c1e2b3a-4d5f-4a6b-8c9d-0e1f2a3b4c5d"
CURSOR_SESSION: Final = "cursor-corpus"
IDS: Final[frozenset[str]] = frozenset({SESSION})
RECORD: Final = (
    "record",
    "--root",
    "{home}",
    "--spool",
    "{spool}",
    "--project",
    "{project}",
)
MOUNTS: Final = (
    "--root",
    "{home}",
    "--mount",
    "scope={user},path=/user",
    "--mount",
    "scope={proj},path=/project",
)
_CLIENTS: Final = ("codex", "muse-code", "pi", "opencode")


def _cursor(event: str, **fields: Any) -> dict[str, Any]:
    return {
        "conversation_id": CURSOR_SESSION,
        "generation_id": "generation-1",
        "cursor_version": "2026.09.10-fd3934a",
        "hook_event_name": event,
        "workspace_roots": ["{project}"],
        **fields,
    }


def _rpc(
    id_: int, method: str, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _cli() -> list[Case]:
    docs = [
        Case("docs listing", ("docs",)),
        Case("docs listing json", ("docs", "--json")),
        Case("docs page", ("docs", "cli")),
        Case("docs page json", ("docs", "cli", "--json")),
        Case("docs quickstart", ("docs", "quickstart")),
        Case("docs unknown", ("docs", "nope")),
        Case("docs unknown json", ("docs", "nope", "--json")),
    ]
    memory = [
        Case("memory index", ("memory", "view", "/", *MOUNTS)),
        Case("memory index json", ("memory", "view", "/", *MOUNTS, "--json")),
        Case("memory view document", ("memory", "view", "/project/facts", *MOUNTS)),
        Case(
            "memory view skill json",
            ("memory", "view", "/project/skills/release", *MOUNTS, "--json"),
        ),
        Case("memory view directory", ("memory", "view", "/user", *MOUNTS)),
        Case(
            "memory create",
            (
                "memory",
                "create",
                "/project/notes/corpus",
                "--content",
                "# Corpus\n\nwritten by the replay\n",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory str_replace",
            (
                "memory",
                "str_replace",
                "/project/facts",
                "--old-str",
                "eu-west-1 (v1)",
                "--new-str",
                "eu-west-2 (v2)",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory insert",
            (
                "memory",
                "insert",
                "/project/decisions",
                "--insert-line",
                "3",
                "--insert-text",
                "- 2026-10-10: the binary\n",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory delete",
            ("memory", "delete", "/project/new-name", *MOUNTS, "--json"),
            writes=True,
        ),
        Case(
            "memory rename",
            (
                "memory",
                "rename",
                "/project/decisions",
                "/project/choices",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory versions json",
            ("memory", "versions", "/project/facts", *MOUNTS, "--json"),
        ),
        Case(
            "memory redact",
            ("memory", "redact", "/project/facts", *MOUNTS, "--json"),
            writes=True,
        ),
        Case(
            "memory revert",
            ("memory", "revert", "/user/profile", "--version", "3", *MOUNTS, "--json"),
            writes=True,
        ),
    ]
    operator = [
        Case(
            "audit scope json",
            ("audit", "--scope", "{proj}", "--root", "{home}", "--json"),
        ),
        Case("search json", ("search", "turn", "--root", "{home}", "--json")),
        Case(
            "messages list json",
            ("messages", "list", "--root", "{home}", "--scope", "{proj}", "--json"),
        ),
        Case(
            "continue json",
            ("continue", "--root", "{home}", "--scope", "{proj}", "--json"),
        ),
        Case("status json", ("status", "--json")),
        Case(
            "prune dry run json",
            ("prune", "--older-than", "1d", "--dry-run", "--root", "{home}", "--json"),
        ),
        Case(
            "export json",
            ("export", "{out}", "--root", "{home}", "--json"),
            writes=True,
        ),
    ]
    version = Case("version", ("version",), token=re.compile(r"v[0-9][0-9A-Za-z.]*"))
    return [version, *docs, *memory, *operator]


def _hooks() -> list[Case]:
    claude = [
        Case(
            "claude-code session start",
            RECORD,
            payload=session_start("startup", session=SESSION),
        ),
        Case(
            "claude-code session compact",
            RECORD,
            payload=session_start("compact", session=SESSION),
        ),
        Case(
            "claude-code session resume",
            RECORD,
            payload=session_start("resume", session=SESSION),
        ),
        Case(
            "claude-code prompt",
            RECORD,
            payload=prompt("Add a test for login", session=SESSION),
            writes=True,
        ),
        Case("claude-code tool", RECORD, payload=tool(session=SESSION), writes=True),
        Case(
            "claude-code subagent tool",
            RECORD,
            payload=tool(session=SESSION, agent_id="sub-1"),
            writes=True,
        ),
        Case(
            "claude-code stop alone", RECORD, payload=stop(session=SESSION), writes=True
        ),
    ]
    others = [
        Case(
            f"{client} {event}",
            (*RECORD, "--agent", client),
            payload=payload,
            writes=event != "session start",
        )
        for client in _CLIENTS
        for event, payload in (
            ("session start", session_start("startup", session=SESSION)),
            ("prompt", prompt(f"{client} asks", session=SESSION)),
            ("tool", tool(session=SESSION)),
            ("stop alone", stop(session=SESSION)),
        )
        if not (client == "opencode" and event == "session start")
    ]
    cursor = [
        Case(
            "cursor session start",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("sessionStart"),
        ),
        Case(
            "cursor prompt",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("beforeSubmitPrompt", prompt="hello"),
            writes=True,
        ),
        Case(
            "cursor tool",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor(
                "postToolUse",
                tool_name="Shell",
                tool_use_id="call-1",
                tool_input={"command": "pwd"},
                tool_output="/work",
            ),
            writes=True,
        ),
        Case(
            "cursor response",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("afterAgentResponse", text="done"),
            writes=True,
        ),
        Case(
            "cursor stop",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("stop", status="completed"),
            writes=True,
        ),
    ]
    return [*claude, *others, *cursor]


def _mcp() -> list[Case]:
    initialize = _rpc(
        1,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "differential", "version": "0"},
        },
    )
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    reads = (
        initialize,
        initialized,
        _rpc(2, "tools/list"),
        _rpc(3, "prompts/list"),
        _rpc(
            4,
            "tools/call",
            {"name": "memory", "arguments": {"command": "view", "path": "/"}},
        ),
        _rpc(
            5, "tools/call", {"name": "search_history", "arguments": {"query": "turn"}}
        ),
        _rpc(6, "tools/call", {"name": "continue_session", "arguments": {}}),
    )
    writes = (
        initialize,
        initialized,
        _rpc(
            2,
            "tools/call",
            {
                "name": "handoff",
                "arguments": {"note": "The replay departs; pick up the release."},
            },
        ),
    )
    return [
        Case("mcp reads", ("mcp", *MOUNTS), rpc=reads),
        Case("mcp handoff", ("mcp", *MOUNTS), rpc=writes, writes=True),
    ]


CASES: Final[tuple[Case, ...]] = (*_cli(), *_hooks(), *_mcp())
BY_NAME: Final[dict[str, Case]] = {case.name: case for case in CASES}
