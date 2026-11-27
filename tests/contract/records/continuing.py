"""A two-turn conversation and the handoff it is delivered as, from DESIGN §33
and §40.54 items 15.2 and 19 alone.

The turns are written whole through `append_turn` to a server without
records, and as records through `write_records`, built by item 3.3's rule
as written here, to a server that keeps them. The handoff note is the
document §33 describes, written through `memory/write`. Every expected
answer is a literal composed here from the frame's released wording and
the turn times the server itself answers; nothing is taken from what the
binary prints.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from tests.contract.records.recalling import run
from tests.contract.records.support import record, write
from tests.contract.support import Binary, Server

CONVERSATION = "continue-parity"
SCOPE = "user:contract"
NOTE = "Pick up the views."
NOTE_ACTOR = "codex:1"
WRITTEN = "2026-11-27T09:00:00Z"


def call(identifier: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": identifier, "name": name, "arguments": arguments}


TURNS: list[list[dict[str, Any]]] = [
    [
        {"role": "user", "content": "Fix the parser"},
        {
            "role": "assistant",
            "content": "Reading it.",
            "tool_calls": [call("t1", "Read", {"file_path": "/w/a.py"})],
        },
        {"role": "tool", "content": "def parse(): pass", "tool_call_id": "t1"},
        {"role": "assistant", "content": "Fixed."},
    ],
    [
        {"role": "user", "content": "Run the tests"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [call("t2", "Bash", {"command": "pytest -q"})],
        },
        {"role": "tool", "content": "3 passed", "tool_call_id": "t2"},
        {"role": "assistant", "content": "All green."},
    ],
]

KINDS = {
    "user": "prompt",
    "assistant": "reply",
    "system": "system",
    "tool": "tool_result",
}


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def records_of(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Item 3.3 for text or absent content: per message its content, then
    each call's arguments; the first record carries the content form; a
    message with neither content nor calls is one empty piece."""
    items: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role, text = message["role"], message.get("content")
        calls = message.get("tool_calls", [])
        result = message.get("tool_call_id") if role == "tool" else None
        form = "none" if text is None else "text"
        pieces: list[tuple[str, str, dict[str, Any]]] = []
        if text is not None or not calls:
            pieces.append((KINDS[role], text or "", {"call_id": result}))
        for at, made in enumerate(calls):
            named = {"call": at, "call_id": made["id"], "name": made["name"]}
            pieces.append(("tool_call", compact(made["arguments"]), named))
        for number, (kind, piece, named) in enumerate(pieces):
            content = form if number == 0 else None
            items.append(
                record(len(items), index, role, kind, piece, content=content, **named)
            )
    return items


def store(kept: Server, legacy: Server) -> None:
    """The same turns, as records to one server and whole to the other."""
    for number, messages in enumerate(TURNS, 1):
        write(kept, CONVERSATION, f"d-{number}", records_of(messages), seal=True)
        legacy.post(
            "conversation/append_turn", conversation_id=CONVERSATION, messages=messages
        )


def note(server: Server) -> None:
    """A pending baton linked to the conversation (§33)."""
    content = (
        f"# handoff from {NOTE_ACTOR}\n\n- from: {NOTE_ACTOR}\n- written: {WRITTEN}\n"
        f"- status: pending\n- conversation: {CONVERSATION}\n\n{NOTE}\n"
    )
    server.post("memory/write", scope=SCOPE, path="handoff", content=content)


def pending(server: Server) -> bool:
    document = server.post("memory/read", scope=SCOPE, path="handoff")
    return "- status: pending\n" in json.dumps(document).replace("\\n", "\n")


def minutes(stamp: str) -> str:
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return moment.strftime("%Y-%m-%dT%H:%MZ")


def ends(server: Server, records: bool) -> tuple[str, str, str]:
    """The last turn's actor and both turn times, as the server answers them:
    from the record headers when it keeps records, else from its turns."""
    if records:
        body = {"conversation_id": CONVERSATION, "kinds": []}
        turns = server.post("conversation/read_records", **body)["turns"]
    else:
        answer = server.post("conversation/read_turns", conversation_id=CONVERSATION)
        turns = answer["turns"]
    actor = turns[-1]["actor"] or "-"
    return actor, minutes(turns[0]["created_at"]), minutes(turns[-1]["created_at"])


VIEWS = {
    "prompts": "[view prompts: the user prompts only, the newest verbatim and the older ones one line each]",
    "calls": "[view calls: prompts, replies and every tool call with its arguments, each result by its size alone]",
    "full": "[view full: as the default view, with each tool result's head, tail and size; within 32,768 characters]",
}
FOOTER = (
    "[end — you are continuing this conversation: carry on from its last answer "
    "as the same work, in the same voice; the user's next message is its next turn]"
)


def expected(view: str, facts: tuple[str, str, str], with_note: bool) -> str:
    """Item 19's views of the two turns, framed as §33 frames a handoff."""
    actor, first, last = facts
    lines = [
        f"[continuing conversation {CONVERSATION} — written by {actor}, 2 turns, "
        f"{first} to {last}; every user prompt and final answer verbatim, tool "
        f'rounds one line each; recall_turn(n, conversation="{CONVERSATION}") '
        "re-reads any turn whole]"
    ]
    if view in VIEWS:
        lines.append(VIEWS[view])
    if with_note:
        lines += [
            f"[its handoff note, written 2026-11-27T09:00Z by {NOTE_ACTOR}]",
            NOTE,
        ]
        lines.append("[end of note]")
    read, bash = ('Read({"file_path":"/w/a.py"})', 'Bash({"command":"pytest -q"})')
    outcome = {
        "light": ("def parse(): pass", "3 passed"),
        "calls": ("[17 bytes]", "[8 bytes]"),
        "full": ("def parse(): pass [17 bytes]", "3 passed [8 bytes]"),
    }
    if view == "prompts":
        lines += ["[1] USER: Fix the parser", "[2] USER: Run the tests"]
    else:
        first_result, second_result = outcome[view]
        lines += ["[1] USER: Fix the parser", "AGENT: Reading it."]
        lines += [f"TOOL {read} → {first_result}", "AGENT: Fixed."]
        lines += ["[2] USER: Run the tests", f"TOOL {bash} → {second_result}"]
        lines.append("AGENT: All green.")
    lines.append(FOOTER)
    return "\n".join(lines)


def tools(
    binary: Binary, url: str, token: str, calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """`continue_session` calls over stdio MCP against `url`; each `result`."""
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
    for number, arguments in enumerate(calls, 1):
        params = {"name": "continue_session", "arguments": arguments}
        requests.append(
            {"jsonrpc": "2.0", "id": number, "method": "tools/call", "params": params}
        )
    stdin = "".join(json.dumps(request) + "\n" for request in requests)
    env = {"NEOSIAN_CLIENT_TOKEN": token}
    flags = ["--url", url, "--scope", SCOPE]
    process = run(binary, "mcp", *flags, env=env, stdin=stdin)
    assert process.returncode == 0, process.stderr
    answers = [json.loads(line) for line in process.stdout.splitlines()]
    return [answer["result"] for answer in answers[1:]]
