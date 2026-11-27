"""The session listing's `recorded` and `size` lines (DESIGN §40.54 item 19,
#448; the T1fd slice and the plan's listing accounting), read with `neosian
memory view`. Turns close through the real hooks with both landing slots held
and land with `neosian record-land` in the foreground (ruling D11). On a home
that keeps records the two lines are cumulative over the turns landed, the
kinds in a fixed order and the size in UTF-8 bytes; a twin home without
records lists neither; an erasure that covers the counted turns removes them
and the next landing starts the count again from its own turn. A turn's size
is computed here from the twin's whole turn: every message's text and every
call's arguments as compact JSON."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import slots_held
from tests.contract.support import Binary

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")

SESSION = "listing-session"
SCOPE = "user:contract"
OUTPUT = 'é 中 😀 "quoted"\n' * 40
KINDS = "prompt, reply, tool_call, tool_result"


def hook(binary: Binary, name: str, **payload: Any) -> Any:
    with slots_held(binary.home):
        result = subprocess.run(
            [str(binary.executable), "record", "--scope", SCOPE, "--json"],
            input=json.dumps(
                {"session_id": SESSION, "hook_event_name": name, **payload}
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


def land(binary: Binary) -> None:
    """`neosian record-land --home H` in the foreground; whatever is queued."""
    landed = binary.run("record-land", "--home", str(binary.home))
    assert landed.returncode == 0, landed.stdout + landed.stderr


def exchange(binary: Binary, prompt: str, tools: int) -> None:
    hook(binary, "UserPromptSubmit", prompt=prompt)
    for n in range(tools):
        hook(
            binary,
            "PostToolUse",
            tool_name="Bash",
            tool_use_id=f"toolu_{prompt}_{n}",
            tool_input={"command": f"cat é-{n}.txt"},
            tool_response=OUTPUT,
        )
    hook(binary, "Stop", last_assistant_message=f"{prompt}: done ✓")
    land(binary)


def listing(binary: Binary) -> str:
    """The sessions document as `memory view` prints it, its path line and
    line numbers taken off."""
    viewed = binary.run(
        "memory", "view", f"/memories/sessions/{SESSION}", "--scope", SCOPE
    )
    assert viewed.returncode == 0, viewed.stderr
    numbered = viewed.stdout.split("\n")[1:-1]
    return "\n".join(line.split(": ", 1)[1] for line in numbered)


def sizes(home: Path) -> list[int]:
    """Each whole turn's UTF-8 bytes, piece by piece."""
    log = home / "conversations" / SESSION / "turns.jsonl"
    totals = []
    for line in log.read_text().splitlines():
        messages = json.loads(line)["messages"]
        pieces = [m["content"] for m in messages if m["content"]]
        pieces += [
            json.dumps(c["arguments"], separators=(",", ":"), ensure_ascii=False)
            for m in messages
            for c in m.get("tool_calls") or []
        ]
        totals.append(sum(len(piece.encode()) for piece in pieces))
    return totals


def tail(turns: int, pair: str = "") -> str:
    return f"- turns: {turns}\n{pair}"


def test_records_landings_list_cumulative_kinds_and_bytes_and_a_plain_home_none(
    binary: Binary, tmp_path: Path
) -> None:
    twin = replace(binary, directory=tmp_path / "twin")
    twin.home.mkdir(parents=True)
    binary.home.mkdir(parents=True, exist_ok=True)
    enabled = binary.run("records", "enable", "--root", str(binary.home))
    assert enabled.returncode == 0, enabled.stderr
    for home in (binary, twin):
        exchange(home, "first é", 2)
    first, *_ = sizes(twin.home)
    assert first == len("first é".encode()) + 2 * (
        len(OUTPUT.encode()) + len('{"command":"cat é-0.txt"}'.encode())
    ) + len("first é: done ✓".encode())
    pair = f"- recorded: {KINDS}\n- size: {first} bytes\n"
    assert listing(binary).endswith(tail(1, pair)), listing(binary)
    for home in (binary, twin):
        exchange(home, "second", 0)
    second = sizes(twin.home)[1]
    assert second == len(b"second") + len("second: done ✓".encode())
    pair = f"- recorded: {KINDS}\n- size: {first + second} bytes\n"
    assert listing(binary).endswith(tail(2, pair)), listing(binary)
    plain = listing(twin)
    assert plain.endswith(tail(2)) and "- recorded:" not in plain, plain
    assert listing(binary).replace(pair, "") == plain.replace(
        plain.split("\n")[4], listing(binary).split("\n")[4]
    ), "the rest of the listing is the plain home's, its start time apart"


def test_an_erasure_covering_the_count_clears_it_and_the_next_turn_starts_again(
    binary: Binary,
) -> None:
    binary.home.mkdir(parents=True, exist_ok=True)
    enabled = binary.run("records", "enable", "--root", str(binary.home))
    assert enabled.returncode == 0, enabled.stderr
    exchange(binary, "one", 1)
    exchange(binary, "two", 0)
    assert "- recorded: " in listing(binary)
    erased = binary.json("redact", SESSION, "--through", "1")
    assert (erased["count"], erased["documents"]) == (1, [])
    assert listing(binary).endswith(tail(2)), listing(binary)
    exchange(binary, "three", 0)
    three = len(b"three") + len("three: done ✓".encode())
    pair = f"- recorded: prompt, reply\n- size: {three} bytes since turn 3\n"
    assert listing(binary).endswith(tail(3, pair)), listing(binary)
    assert binary.json("redact", SESSION, "--turns", "1,2")["count"] == 2
    assert listing(binary).endswith(tail(3, pair)), "turns 1 and 2 are not counted"
    binary.json("redact", SESSION, "--turns", "3")
    assert listing(binary).endswith(tail(3)), listing(binary)
