"""The re-review of 4ed54ba (record/x2b-review-2026-11-21.md, findings A to
H): what a write child's session loads, the watcher's rules, what a
read-only child may do with neosian, where a server may host children, and
the bounds of every answer."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.harness_support import Harnesses, Session, alive, end, eventually
from tests.contract.support import Binary

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the tool is absent on Windows")


@pytest.fixture
def harnesses(binary: Binary) -> Harnesses:
    return Harnesses(binary).install()


def finished(session: Session, child: str, seconds: int = 30) -> Any:
    deadline = time.monotonic() + seconds
    while True:
        answer = session.harness(command="wait", child=child, seconds=5)
        if answer["state"] != "running" or time.monotonic() > deadline:
            return answer


def sent(session: Session, **arguments: Any) -> tuple[bool, str]:
    """The answer's text exactly as the server sent it."""
    result = session.request("tools/call", {"name": "harness", "arguments": arguments})[
        "result"
    ]
    return result["isError"], result["content"][0]["text"]


def test_a_write_child_loads_nothing_the_project_holds(
    binary: Binary, harnesses: Harnesses
) -> None:
    """A: whatever a write child writes (in any letter case) is not loaded
    in its own session."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    for mode in ("read-only", "write"):
        child = session.harness(
            command="start", agent="opencode", prompt="x", mode=mode
        )
        finished(session, child["child"])
        session.harness(command="stop", child=child["child"])
    assert [run["project_config_off"] for run in harnesses.runs()] == ["1", "1"]
    session.close()


def watch(binary: Binary, env: dict[str, str], data: bytes, hold: bool = False) -> Any:
    process = subprocess.Popen(
        [str(binary.executable), "harness-watch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=binary.directory / "work",
    )
    assert process.stdin is not None
    try:
        process.stdin.write(data)
        process.stdin.flush()
        if not hold:
            process.stdin.close()
    except BrokenPipeError:
        pass
    try:
        process.wait(timeout=30)
    finally:
        if not process.stdin.closed:
            process.stdin.close()
    assert process.stdout is not None and process.stderr is not None
    out, err = process.stdout.read(), process.stderr.read()
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)


def plan(directory: Path, **extra: Any) -> bytes:
    fields = {
        "agent": "codex",
        "mode": "read-only",
        "directory": str(directory.resolve()),
        "prompt_bytes": 2,
    }
    fields.update(extra)
    return json.dumps(fields).encode() + b"\nhi"


def test_the_watcher_applies_exactly_the_tools_rules(
    binary: Binary, harnesses: Harnesses
) -> None:
    """C: no weaker directory rule, a refusal without HOME, and store flags
    only in the forms `neosian mcp` accepts."""
    harnesses.enable()
    work = harnesses.work
    env = harnesses.env()
    no_home = {key: value for key, value in env.items() if key != "HOME"}
    for environment in (no_home, env | {"HOME": ""}):
        refused = watch(binary, environment, plan(work))
        assert refused.returncode == 2, refused.stderr
    for directory in (binary.directory, binary.directory.parent, Path("/")):
        refused = watch(binary, env, plan(directory))
        assert refused.returncode == 2, directory
    for store in (
        ["--url", "ftp://example.com"],
        ["--url", "not a url"],
        ["--root", "relative/root"],
        ["--root", str(binary.home), "--url", "https://example.com"],
        ["--root", str(binary.home), "--root", str(binary.home)],
        ["--mount", "path=user"],
        ["--profile", "Bad Name"],
        ["--scope", "no scope"],
        ["--schema", "Bad;Schema"],
        ["--actor", "mcp:x"],
    ):
        refused = watch(binary, env, plan(work, store=store))
        assert refused.returncode == 2, store
    assert harnesses.runs() == []
    accepted = watch(
        binary,
        env,
        plan(work, store=["--root", str(binary.home), "--scope", "user:contract"]),
        hold=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert len(harnesses.runs()) == 1


def test_a_read_only_claude_child_gets_neosian_read_only(
    binary: Binary, harnesses: Harnesses
) -> None:
    """D: the child's strict configuration starts neosian with every mount
    read-only; reads answer, writes are refused."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    child = session.harness(command="start", agent="claude-code", prompt="probe-ro")
    finished(session, child["child"])
    server = harnesses.runs()[0]["read_only_server"]
    mounts = [
        server["args"][i + 1]
        for i, word in enumerate(server["args"])
        if word == "--mount"
    ]
    assert len(mounts) == 2 and all(mount.endswith(",ro") for mount in mounts)
    assert server["args"][server["args"].index("--root") + 1] == str(binary.home)
    assert not server["view_error"]
    assert server["create_error"] and "is read-only" in server["create_text"]
    assert "handoff" not in server["tools"] and "harness" not in server["tools"]
    session.close()


def test_a_read_only_codex_child_reaches_only_neosian_read_only(
    binary: Binary, harnesses: Harnesses
) -> None:
    """D: every other configured MCP server is switched off for the run and
    neosian's own entry gets read-only arguments; write keeps them all."""
    harnesses.enable()
    env = harnesses.env(STANDIN_CODEX_SERVERS='["playwright", "sentry-1"]')
    session = Session(binary, env)
    for prompt, mode in (("probe-ro", "read-only"), ("x", "write")):
        child = session.harness(
            command="start", agent="codex", prompt=prompt, mode=mode
        )
        finished(session, child["child"])
        session.harness(command="stop", child=child["child"])
    read_only, write = harnesses.runs()
    assert read_only["switched_off"] == ["playwright", "sentry-1"]
    server = read_only["read_only_server"]
    assert server["create_error"] and "is read-only" in server["create_text"]
    assert not server["view_error"]
    assert write["switched_off"] == []
    assert not any(".args=" in word for word in write["argv"])
    session.close()


def test_a_codex_server_that_cannot_be_switched_off_refuses_read_only(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_CODEX_SERVERS='["we.ird"]'))
    refused = session.call("harness", command="start", agent="codex", prompt="x")
    assert refused[0] and "we.ird" in refused[1], refused
    assert harnesses.runs() == []
    child = session.harness(command="start", agent="codex", prompt="x", mode="write")
    assert finished(session, child["child"])["state"] == "finished"
    session.close()


@pytest.mark.parametrize("case", ["ancestor", "empty-home", "no-home"])
def test_a_server_directory_counts_only_strictly_below_home(
    binary: Binary, harnesses: Harnesses, case: str
) -> None:
    """E: anywhere but strictly below the home directory, or with HOME unset
    or empty, a directory must be listed."""
    harnesses.enable()
    env = harnesses.env()
    cwd = harnesses.work
    if case == "ancestor":
        cwd = binary.directory.parent
    elif case == "empty-home":
        env["HOME"] = ""
    else:
        del env["HOME"]
    session = Session(binary, env, cwd=cwd)
    refused = session.call("harness", command="start", agent="codex", prompt="x")
    assert refused[0] and "list a directory" in refused[1], refused
    session.close()


def test_the_answer_arrives_before_leftovers_are_ended(
    binary: Binary, harnesses: Harnesses
) -> None:
    """G: the result is there when the harness exits; what it left in its
    group is ended later, after a longer grace."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    started = time.monotonic()
    session.harness(command="start", agent="opencode", prompt="leave")
    done = finished(session, "h1")
    run = harnesses.runs()[0]
    try:
        assert done["state"] == "finished" and time.monotonic() - started < 6
        assert alive(run["left"])
        assert eventually(lambda: not alive(run["left"]), 40)
    finally:
        end(run["left"])
    session.close()


def test_events_never_exceed_their_stated_cap(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="events:100 big:40000")
    done = finished(session, "h1")
    assert len(done["events"]) <= 32
    kinds = [event["kind"] for event in done["events"]]
    assert kinds.count("truncated") == 2
    session.close()


def test_the_answer_is_cut_with_its_size_stated(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="claude-code", prompt="big-answer:40000")
    done = finished(session, "h1")
    assert len(done["answer"].encode()) <= 16 * 1024
    assert done["answer_bytes"] == 40000
    assert any("The answer is cut after" in note for note in done["notes"])
    session.close()


def test_list_cuts_errors_and_trims_its_oldest_children_by_bytes_sent(
    binary: Binary, harnesses: Harnesses
) -> None:
    # One path length on every system: the temporary root is shorter on Linux than
    # on macOS, and the list must pass its bound wherever the test runs.
    deep = binary.directory
    while len(str(deep)) < 990:
        deep = deep / ("d" * min(200, 1000 - len(str(deep)) - 1))
    deep.mkdir(parents=True)
    harnesses.enable(directories=[deep], max_children=8)
    session = Session(binary, harnesses.env())
    for _ in range(32):
        child = session.harness(
            command="start",
            agent="opencode",
            prompt="huge-fail",
            directory=str(deep),
            model="m" * 128,
        )
        finished(session, child["child"])
        session.harness(command="stop", child=child["child"])
    is_error, text = sent(session, command="list")
    assert not is_error
    assert len(text.encode()) <= 64 * 1024
    listed = json.loads(text)
    assert listed["omitted"] > 0
    assert listed["omitted"] + len(listed["children"]) == 32
    assert listed["children"][-1]["child"] == "h32"
    for child in listed["children"]:
        assert len(child["error"].encode()) <= 512
        assert "text" not in child and "answer" not in child
    session.close()
