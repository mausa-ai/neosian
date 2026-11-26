"""Turns land as records in parts on a local store (DESIGN §40.54 items 7, 9
and 17.3; the T1e1b slice of the T1e1 plan). After `neosian records enable`
a Stop writes its turn as one draft in parts and seals it: the conversation
holds a format-2 header row, and `read_turns` answers the turn as a twin home
without records answers its `append_turn` turn. A turn over the store's
`NEOSIAN_MAX_RECORD_TURN_BYTES` is held with the store's status, code and
message, its Stop says so (ruling E10), and the next turn lands; an older
turn held that way never fails the Stop whose own turn lands. Expected values
are literal or computed here."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tests.contract.landing.conftest import Hold
from tests.contract.support import ADMIN, Binary, Server, serve

SESSION = "records-session"
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")
STAMP = re.compile(rb'"created_at":"[^"]*"')
REFUSAL = (
    "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: {bytes} > 65536; "
    "this store does not keep a turn this large"
)


def hook(
    binary: Binary, name: str, env: dict[str, str] | None = None, **payload: Any
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary.executable), "record", "--scope", "user:contract", "--json"],
        input=json.dumps({"session_id": SESSION, "hook_event_name": name, **payload}),
        env={**binary.environment(), **(env or {})},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def ok(result: subprocess.CompletedProcess[str]) -> Any:
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def enable(binary: Binary) -> None:
    binary.home.mkdir(parents=True, exist_ok=True)
    enabled = binary.run("records", "enable", "--root", str(binary.home))
    assert enabled.returncode == 0, enabled.stderr


def rows(binary: Binary) -> list[Any]:
    log = binary.home / "conversations" / SESSION / "turns.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


def raw_turns(server: Server) -> bytes:
    """`read_turns` of the conversation, the answer's bytes as sent."""
    body = json.dumps({"conversation_id": SESSION}).encode()
    request = Request(
        server.url + "/v1/conversation/read_turns",
        data=body,
        headers={"Authorization": f"Bearer {ADMIN}"},
        method="POST",
    )
    with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
        assert response.status == 200
        return bytes(response.read())


def exchange(binary: Binary) -> Any:
    """A prompt, two tool rounds of about 900 KB each (escapes, multi-byte
    text), a reply: more than one part's worth."""
    ok(hook(binary, "UserPromptSubmit", prompt='build "it" \\ é 中 😀'))
    for n in range(2):
        output = f'line {n} \t é 中 😀 "quoted"\n' * 30_000
        ok(
            hook(
                binary,
                "PostToolUse",
                tool_name="Bash",
                tool_use_id=f"toolu_{n}",
                tool_input={"command": f"make part-{n}"},
                tool_response=output,
            )
        )
    return ok(hook(binary, "Stop", last_assistant_message="built\nall of it"))


def test_a_stop_lands_a_records_turn_that_reads_as_its_append_turn_twin(
    binary: Binary, tmp_path: Path
) -> None:
    twin = Binary(binary.executable, tmp_path / "twin")
    twin.directory.mkdir()
    enable(binary)
    for home in (binary, twin):
        landed = exchange(home)
        assert (landed["disposition"], landed["turn"]) == ("recorded", 1)
        assert not (home.home / "spool" / "pending" / SESSION).exists()
    (header,) = rows(binary)
    assert list(header) == [
        "neosian_format",
        "turn",
        "created_at",
        "actor",
        "records",
        "bytes",
        "draft",
    ]
    assert header["neosian_format"] == 2 and header["turn"] == 1
    assert header["actor"] == f"claude-code:{SESSION}"
    assert re.fullmatch(r"p[0-9a-f]{32}", header["draft"])
    stored = binary.home / "conversations" / SESSION / "records" / "1.jsonl"
    assert len(stored.read_text().splitlines()) == header["records"]
    (whole,) = rows(twin)
    assert whole["neosian_format"] == 1 and len(whole["messages"]) == 6
    # The turn's bytes: every piece's UTF-8 size, from the twin's messages.
    pieces = [m["content"] for m in whole["messages"] if m["content"]] + [
        json.dumps(c["arguments"], separators=(",", ":"), ensure_ascii=False)
        for m in whole["messages"]
        for c in m.get("tool_calls") or []
    ]
    assert header["bytes"] == sum(len(p.encode()) for p in pieces) > 983_040
    answers = []
    for home in (binary, twin):
        with serve(home) as server:
            answers.append(raw_turns(server))
    assert STAMP.sub(b'"created_at":""', answers[0]) == STAMP.sub(
        b'"created_at":""', answers[1]
    )
    assert len(STAMP.findall(answers[0])) == 1


def test_a_turn_of_2100_tool_rounds_lands_whole(binary: Binary) -> None:
    enable(binary)
    span = [{"kind": "prompt", "text": "many rounds"}] + [
        {
            "kind": "tool",
            "id": f"call-{n}",
            "name": "Bash",
            "input": {"command": f"echo {n}"},
            "response": str(n),
        }
        for n in range(2_100)
    ]
    spool = binary.home / "spool"
    spool.mkdir(parents=True)
    (spool / f"{SESSION}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in span)
    )
    landed = ok(hook(binary, "Stop", last_assistant_message="all done"))
    assert (landed["disposition"], landed["turn"]) == ("recorded", 1)
    (header,) = rows(binary)
    # One record per message: more than two parts of 2,048 records.
    assert (header["neosian_format"], header["records"]) == (2, 4_202)
    with serve(binary) as server:
        (turn,) = server.post("conversation/read_turns", conversation_id=SESSION)[
            "turns"
        ]
    messages = turn["messages"]
    assert len(messages) == 4_202
    assert messages[0]["content"] == "many rounds"
    assert messages[-1]["content"] == "all done"
    assert messages[-2] == {
        "role": "tool",
        "content": "2099",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": "call-2099",
    }
    assert messages[-3]["tool_calls"] == [
        {"id": "call-2099", "name": "Bash", "arguments": {"command": "echo 2099"}}
    ]


def test_a_turn_over_the_store_s_bound_is_held_with_its_message(
    binary: Binary,
) -> None:
    enable(binary)
    bound = {"NEOSIAN_MAX_RECORD_TURN_BYTES": "65536"}
    ok(hook(binary, "UserPromptSubmit", env=bound, prompt="x" * 70_000))
    refused = hook(binary, "Stop", env=bound, last_assistant_message="done")
    message = REFUSAL.format(bytes=70_004)
    held = binary.home / "spool" / "held" / SESSION
    error = f"the turn is held and will not be sent again: {message}"
    hint = (
        f"the record never blocks the agent; the turn stays in {held / '1.jsonl'} "
        f"with its reason in {held / '1.reason'}, and nothing sends it again by itself"
    )
    assert refused.returncode == 1
    assert json.loads(refused.stdout) == {"error": error, "hint": hint}
    assert refused.stderr == f"error: {error}\nhint: {hint}\n"
    assert json.loads((held / "1.reason").read_text()) == {
        "reason": "refused",
        "status": 413,
        "code": "value_error",
        "message": message,
    }
    assert b"x" * 70_000 in (held / "1.jsonl").read_bytes()
    assert json.loads((held / "1.state").read_text())["next_seq"] == 0
    drafts = binary.home / "conversations" / SESSION / ".drafts"
    assert not drafts.exists() or not list(drafts.iterdir())
    assert not (binary.home / "conversations" / SESSION / "turns.jsonl").exists()
    ok(hook(binary, "UserPromptSubmit", env=bound, prompt="small"))
    landed = ok(hook(binary, "Stop", env=bound, last_assistant_message="fits"))
    assert (landed["disposition"], landed["turn"]) == ("recorded", 1)
    (header,) = rows(binary)
    assert (header["neosian_format"], header["bytes"]) == (2, len("smallfits"))
    assert (held / "1.jsonl").is_file()


@POSIX
def test_an_older_turn_refused_is_held_while_the_stop_s_own_turn_lands(
    binary: Binary, hold_lock: Hold
) -> None:
    enable(binary)
    ok(hook(binary, "UserPromptSubmit", prompt="x" * 70_000))
    with hold_lock(binary.home / "spool" / "pending" / SESSION):
        assert ok(hook(binary, "Stop", last_assistant_message="done"))[
            "disposition"
        ] == ("pending")
    bound = {"NEOSIAN_MAX_RECORD_TURN_BYTES": "65536"}
    ok(hook(binary, "UserPromptSubmit", env=bound, prompt="small"))
    landed = ok(hook(binary, "Stop", env=bound, last_assistant_message="fits"))
    assert (landed["disposition"], landed["turn"]) == ("recorded", 1)
    held = binary.home / "spool" / "held" / SESSION
    assert json.loads((held / "1.reason").read_text()) == {
        "reason": "refused",
        "status": 413,
        "code": "value_error",
        "message": REFUSAL.format(bytes=70_004),
    }
    (header,) = rows(binary)
    assert header["bytes"] == len("smallfits")
