"""The common filesystem policy (X4b): on macOS every harness child runs
under one sandbox profile that neosian's watcher applies at launch, and the
kernel itself answers the stand-in child's benign writes. Every write lands
inside pytest's temporary directory; the home directory is a fixture there.
Elsewhere each harness's own preset holds and the answer says so."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.harness_support import (
    CODEX_READ_ONLY,
    CONFINED,
    OUTER_POLICY,
    Harnesses,
    Session,
    alive,
    end,
    eventually,
)
from tests.contract.support import Binary
from tests.contract.test_harness_rereview import finished, plan, watch

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the tool is absent on Windows")
macos = pytest.mark.skipif(not CONFINED, reason="the common policy is macOS only")
AGENTS = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}
STATE = {
    "claude": ".claude/projects",
    "codex": ".codex/sessions",
    "opencode": ".local/share/opencode/log",
}
# A sandbox the server or watcher already runs under, as inside another
# harness's sandbox: any profile but the one neosian applies.
OUTER = [
    "/usr/bin/sandbox-exec",
    "-p",
    '(version 1)(allow default)(deny file-write* (literal "/outer"))',
]


@pytest.fixture
def harnesses(binary: Binary) -> Harnesses:
    return Harnesses(binary).install()


def targets(harnesses: Harnesses, program: str) -> dict[str, str]:
    """The fixture paths each child tries to write, their directories made."""
    home = harnesses.binary.directory
    other = next(p for p in STATE if p != program)
    places = {
        "work": harnesses.work / "probe.txt",
        "sibling": home / "sibling" / "probe.txt",
        "home": home / "probe.txt",
        "spool": harnesses.binary.home / "spool" / "probe.txt",
        "state": home / STATE[program] / "probe.txt",
        "other_state": home / STATE[other] / "probe.txt",
        "protected": harnesses.work / ".claude" / "settings.json",
        "hooks": harnesses.work / ".git" / "hooks" / "pre-commit",
    }
    for path in places.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    return {name: str(path) for name, path in places.items()} | {
        "temp": "$TMPDIR/probe.txt"
    }


def run(harnesses: Harnesses, agent: str, mode: str, words: str) -> dict[str, Any]:
    program = AGENTS[agent]
    env = harnesses.env(STANDIN_WRITES=json.dumps(targets(harnesses, program)))
    session = Session(harnesses.binary, env)
    child = session.harness(command="start", agent=agent, prompt=words, mode=mode)
    done = finished(session, child["child"])
    session.close()
    assert done["state"] == "finished", done
    found = [entry for entry in harnesses.runs() if "writes" in entry]
    assert len(found) == 1, harnesses.runs()
    return {"done": done, "run": found[0]}


@macos
@pytest.mark.parametrize("agent", AGENTS)
def test_a_read_only_child_writes_only_its_temp_and_its_own_state(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    harnesses.enable()
    result = run(harnesses, agent, "read-only", "writes")
    writes = result["run"]["writes"]
    for name in ("temp", "state"):
        assert writes.pop(name) == "written", name
    assert writes == dict.fromkeys(writes, "EPERM"), writes
    assert not (harnesses.work / "probe.txt").exists()
    assert not (binary.directory / "probe.txt").exists()
    # The watcher, outside the policy, records the turn (X4a).
    assert result["done"]["record"]["turns"] == "1", result["done"]


@macos
@pytest.mark.parametrize("agent", AGENTS)
def test_a_write_child_also_writes_its_directory_and_nothing_else(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    harnesses.enable()
    result = run(harnesses, agent, "write", "writes")
    writes = result["run"]["writes"]
    for name in ("work", "temp", "state"):
        assert writes.pop(name) == "written", name
    assert writes == dict.fromkeys(writes, "EPERM"), writes
    assert (harnesses.work / "probe.txt").read_text() == "probe\n"
    assert not (binary.directory / "probe.txt").exists()
    assert result["done"]["record"]["turns"] == "1", result["done"]


@macos
def test_each_harness_runs_inside_the_policy_with_its_markers_kept(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Every child gets the private directory as TMPDIR, keeps the child
    marker, cannot remove it, and its own neosian lists no harness tool;
    Codex runs without its own sandbox, which cannot start inside ours."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    for agent in AGENTS:
        child = session.harness(command="start", agent=agent, prompt="probe")
        assert finished(session, child["child"])["state"] == "finished"
        session.harness(command="stop", child=child["child"])
    session.close()
    runs = {entry["program"]: entry for entry in harnesses.runs()}
    for run in runs.values():
        assert "harness" not in run["child_tools"] and "memory" in run["child_tools"]
        assert run["child_env"] == "1" and Path(run["tmpdir"]).name.startswith(
            "neosian-harness-"
        )
    codex = runs["codex"]["argv"]
    assert codex[2:4] == CODEX_READ_ONLY
    assert "-s" not in codex and 'approval_policy="never"' not in codex
    claude = runs["claude"]["argv"]
    assert claude[claude.index("--permission-mode") + 1] == "dontAsk"


@macos
def test_a_policy_that_cannot_be_applied_starts_no_child(
    binary: Binary, harnesses: Harnesses
) -> None:
    """A server or watcher that already runs inside a sandbox cannot apply
    another profile: start refuses, the watcher refuses, nothing runs."""
    harnesses.enable()
    env = harnesses.env()
    server = subprocess.Popen(
        [*OUTER, str(binary.executable), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=harnesses.work,
    )
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "harness",
                "arguments": {"command": "start", "agent": "codex", "prompt": "x"},
            },
        },
    ]
    out, _ = server.communicate("".join(json.dumps(m) + "\n" for m in lines), 60)
    answer = json.loads(out.splitlines()[-1])["result"]
    assert answer["isError"], answer
    assert "could not apply its filesystem policy" in answer["content"][0]["text"]
    watcher = subprocess.run(
        [*OUTER, str(binary.executable), "harness-watch"],
        input=plan(harnesses.work),
        capture_output=True,
        env=env,
        cwd=harnesses.work,
        timeout=60,
        check=False,
    )
    assert watcher.returncode == 2, watcher.stderr
    assert b"could not apply its filesystem policy" in watcher.stderr
    # Admission comes first: no client command ran at all (X4e P4).
    assert harnesses.entries() == []


@macos
@pytest.mark.parametrize("mode", ["read-only", "write"])
def test_a_store_that_cannot_be_established_refuses_either_mode(
    binary: Binary, harnesses: Harnesses, mode: str
) -> None:
    """The watcher protects the parent's own store, from its flags alone;
    one it cannot resolve could not be protected, so nothing starts (X4e).
    A store reached with a credential starts (`test_harness_hardened.py`)."""
    harnesses.enable()
    store = ["--profile", "not-installed"]
    refused = watch(
        binary, harnesses.env(), plan(harnesses.work, store=store, mode=mode)
    )
    assert refused.returncode == 2, refused.stderr
    assert b"could not establish this server's store" in refused.stderr
    assert harnesses.entries() == []


@macos
@pytest.mark.parametrize("how", ["stop", "limit"])
def test_stop_timeout_and_descendants_end_under_the_policy(
    binary: Binary, harnesses: Harnesses, how: str
) -> None:
    if how == "limit":
        harnesses.enable(turn_seconds=10)
    else:
        harnesses.enable()
    env = harnesses.env(
        STANDIN_WRITES=json.dumps({"home": str(binary.directory / "x")})
    )
    session = Session(binary, env)
    session.harness(command="start", agent="codex", prompt="writes grandchild sleep:60")
    assert eventually(lambda: bool(harnesses.runs()))
    run = harnesses.runs()[0]
    assert run["writes"] == {"home": "EPERM"}, "the child runs confined"
    if how == "stop":
        session.harness(command="stop", child="h1")
    else:
        deadline = time.monotonic() + 40
        while session.harness(command="wait", child="h1", seconds=5)["state"] != (
            "timed_out"
        ):
            assert time.monotonic() < deadline
    try:
        assert eventually(lambda: not alive(run["grandchild"]))
        assert eventually(lambda: not alive(run["pid"]))
    finally:
        end(run["grandchild"])
    session.close()


def test_the_platform_decides_the_boundary_and_the_answer_says_which(
    binary: Binary, harnesses: Harnesses
) -> None:
    """On macOS no note and Codex's own sandbox off; elsewhere today's
    presets exactly, and a note that the common policy is not applied."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="x")
    done = finished(session, "h1")
    session.close()
    argv = harnesses.runs()[0]["argv"]
    said = [note for note in done["notes"] if "only on macOS" in note]
    assert argv[2 : 2 + len(CODEX_READ_ONLY)] == CODEX_READ_ONLY
    if CONFINED:
        assert said == [] and OUTER_POLICY in argv
    else:
        assert OUTER_POLICY not in argv
        assert len(said) == 1 and "codex's own read-only preset" in said[0]
