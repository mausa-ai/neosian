"""The keep limit (DESIGN §40.54 items 17.7 and 23; ruling D12 of the T1e1
plan). With `[record] keep_bytes = 16777216` in the home's `config.toml`, a
record that would pass the limit first deletes the oldest closed turns of
the home and counts them in `spool/landing/drops.json`; a conversation whose
landing lock another process holds keeps its turns; an open span that passes
the limit by itself is deleted and counted with the rest of its turn, and
the next prompt starts a new span. Without the setting the limit is
536,870,912 bytes, and a value out of range refuses the hook. Hooks run with both landing slots
held, so no lander starts. Expected values are literal."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, slots_held
from tests.contract.support import Binary

SESSION = "keep-session"
OTHER = "keep-other"
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")
KEEP = "[record]\nkeep_bytes = 16777216\n"
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")
# A planted span of 6,000,000 bytes: one prompt record.
SPAN = '{"kind": "prompt", "text": "' + "x" * (6_000_000 - 31) + '"}\n'
# The span closed: its 110-byte opening line, the span, the stop's 33 bytes.
TURN = 6_000_143
STOP = '{"kind": "stop", "text": "done"}\n'
TOOL = (
    '{"kind": "tool", "id": "t1", "name": "Bash", "input": {"command": "ls"}, '
    '"response": "ok"}\n'
)
TOOL_EVENT: dict[str, Any] = {
    "tool_name": "Bash",
    "tool_input": {"command": "ls"},
    "tool_response": "ok",
    "tool_use_id": "t1",
}


def hook(binary: Binary, name: str, session: str = SESSION, **payload: Any) -> Any:
    with slots_held(binary.home):
        result = subprocess.run(
            [str(binary.executable), "record", "--scope", "user:contract", "--json"],
            input=json.dumps(
                {"session_id": session, "hook_event_name": name, **payload}
            ),
            env=binary.environment(),
            cwd=binary.directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def spool(binary: Binary) -> Path:
    return binary.home / "spool"


def configure(binary: Binary, text: str) -> None:
    binary.home.mkdir(parents=True, exist_ok=True)
    (binary.home / "config.toml").write_text(text)


def close_span(binary: Binary, session: str = SESSION) -> None:
    """A planted 6,000,000-byte span closed by a Stop into the queue."""
    spool(binary).mkdir(parents=True, exist_ok=True)
    (spool(binary) / f"{session}.jsonl").write_text(SPAN)
    answer = hook(binary, "Stop", session, last_assistant_message="done")
    assert answer["disposition"] == "pending"


def queued(binary: Binary, session: str = SESSION) -> list[int]:
    directory = spool(binary) / "pending" / session
    return sorted(int(turn.stem) for turn in directory.glob("*.jsonl"))


def drops(binary: Binary) -> Any:
    path = spool(binary) / "landing" / "drops.json"
    return json.loads(path.read_text()) if path.exists() else None


def counted(binary: Binary, turns: int, total: int) -> None:
    record = drops(binary)
    assert record is not None
    assert (record["turns"], record["bytes"]) == (turns, total)
    assert list(record) == ["turns", "bytes", "first", "last"]
    assert STAMP.fullmatch(record["first"]) and STAMP.fullmatch(record["last"])
    assert record["first"] <= record["last"]


@POSIX
def test_a_queue_past_the_limit_loses_its_oldest_turns_counted(binary: Binary) -> None:
    configure(binary, KEEP)
    close_span(binary)
    close_span(binary)
    assert queued(binary) == [1, 2]
    states = spool(binary) / "pending" / SESSION
    assert json.loads((states / "1.state").read_text())["bytes"] == TURN
    assert (states / "1.jsonl").stat().st_size == TURN
    # 12,000,286 queued and 6,000,033 open pass 16,777,216: the oldest goes.
    close_span(binary)
    assert queued(binary) == [2, 3]
    assert not (states / "1.state").exists()
    counted(binary, 1, 6_000_143)
    close_span(binary)
    assert queued(binary) == [3, 4]
    counted(binary, 2, 12_000_286)


@POSIX
def test_a_conversation_whose_lock_is_held_keeps_its_turns(
    binary: Binary, hold_lock: Hold
) -> None:
    configure(binary, KEEP)
    close_span(binary, OTHER)
    close_span(binary, OTHER)
    with hold_lock(spool(binary) / "pending" / OTHER):
        # Only the held conversation's turns could go: none does, and the
        # turn still closes past the limit.
        close_span(binary)
        assert queued(binary, OTHER) == [1, 2]
        assert queued(binary) == [1]
        assert drops(binary) is None
    # Released, the next record deletes the oldest turn of the home.
    assert hook(binary, "UserPromptSubmit", prompt="next")["disposition"] == "spooled"
    assert queued(binary, OTHER) == [2]
    assert queued(binary) == [1]
    counted(binary, 1, 6_000_143)


@POSIX
def test_an_open_span_past_the_limit_goes_and_the_next_prompt_starts_anew(
    binary: Binary,
) -> None:
    configure(binary, KEEP)
    spool(binary).mkdir(parents=True, exist_ok=True)
    span = spool(binary) / f"{SESSION}.jsonl"
    text = "x" * (17 * 1024 * 1024 - 31)
    span.write_text('{"kind": "prompt", "text": "' + text + '"}\n')
    assert span.stat().st_size == 17_825_792
    assert hook(binary, "PostToolUse", **TOOL_EVENT)["disposition"] == "dropped"
    assert not span.exists()
    counted(binary, 1, 17_825_792 + 91)
    # The rest of that turn is dropped up to the next prompt.
    assert hook(binary, "Stop", last_assistant_message="done")["disposition"] == "empty"
    assert not span.exists()
    counted(binary, 1, 17_825_916)
    assert hook(binary, "UserPromptSubmit", prompt="again")["disposition"] == "spooled"
    assert span.read_text() == '{"kind": "prompt", "text": "again"}\n'
    landed = hook(binary, "Stop", last_assistant_message="done")
    assert landed["disposition"] == "recorded"
    log = binary.home / "conversations" / SESSION / "turns.jsonl"
    (row,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(m["role"], m["content"]) for m in row["messages"]] == [
        ("user", "again"),
        ("assistant", "done"),
    ]
    counted(binary, 1, 17_825_916)
    assert len(TOOL) == 91 and len(STOP) == 33


@POSIX
def test_without_the_setting_512_mib_apply_and_a_bad_value_refuses(
    binary: Binary,
) -> None:
    for _ in range(3):
        close_span(binary)
    assert queued(binary) == [1, 2, 3]
    assert drops(binary) is None
    # The turns made 200 MiB long (sparse): 400 MiB keep, 600 MiB pass.
    turns = spool(binary) / "pending" / SESSION
    for n in (1, 2):
        os.truncate(turns / f"{n}.jsonl", 200 * 1024 * 1024)
    assert hook(binary, "UserPromptSubmit", prompt="a")["disposition"] == "spooled"
    assert queued(binary) == [1, 2, 3] and drops(binary) is None
    os.truncate(turns / "3.jsonl", 200 * 1024 * 1024)
    assert hook(binary, "PostToolUse", **TOOL_EVENT)["disposition"] == "spooled"
    assert queued(binary) == [2, 3]
    counted(binary, 1, 209_715_200)
    (spool(binary) / f"{SESSION}.jsonl").unlink()
    configure(binary, "[record]\nkeep_bytes = 16777215\n")
    refused = subprocess.run(
        [str(binary.executable), "record", "--scope", "user:contract"],
        input=json.dumps(
            {
                "session_id": SESSION,
                "hook_event_name": "UserPromptSubmit",
                "prompt": "a",
            }
        ),
        env=binary.environment(),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert refused.returncode == 1
    assert refused.stderr.startswith(
        "error: [record] keep_bytes must be an integer in 16777216..68719476736\n"
    ), refused.stderr
    assert not (spool(binary) / f"{SESSION}.jsonl").exists()
    assert queued(binary) == [2, 3]
