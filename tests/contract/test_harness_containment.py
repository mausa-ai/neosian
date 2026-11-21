"""Containment of harness children (record/x2b-review-2026-11-21.md): where
a child may work, what its read-only mode loads, what ends with a turn, what
the watcher accepts, and which words are the child's."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.harness_support import (
    Harnesses,
    Session,
    alive,
    descriptors,
    end,
    eventually,
    threads,
)
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


def notes(answer: Any) -> str:
    return " ".join(answer["notes"])


@pytest.mark.parametrize("agent", ["opencode", "codex", "claude-code"])
def test_pwd_is_the_child_directory(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env(PWD="/"))
    session.harness(command="start", agent=agent, prompt="where")
    finished(session, "h1")
    run = harnesses.runs()[0]
    assert run["pwd"] == str(harnesses.work.resolve()) == run["cwd"]
    session.close()


def test_a_child_works_only_where_the_person_allows(
    binary: Binary, harnesses: Harnesses
) -> None:
    other = binary.directory / "other"
    listed = binary.directory / "listed"
    other.mkdir()
    listed.mkdir()
    harnesses.enable(directories=[listed])
    session = Session(binary, harnesses.env())
    for given in (str(other), "../other", "/", str(binary.directory), str(Path.home())):
        refused = session.call(
            "harness", command="start", agent="codex", prompt="x", directory=given
        )
        assert refused[0] and "is not a directory children may use" in refused[1], (
            given,
            refused,
        )
    started = session.harness(
        command="start", agent="codex", prompt="x", directory=str(listed)
    )
    assert started["directory"] == str(listed.resolve())
    finished(session, "h1")
    assert [run["cwd"] for run in harnesses.runs()] == [str(listed.resolve())]
    session.close()


@pytest.mark.parametrize("where", ["home", "root"])
def test_a_server_in_home_or_root_needs_a_listed_directory(
    binary: Binary, harnesses: Harnesses, where: str
) -> None:
    cwd = binary.directory if where == "home" else Path("/")
    harnesses.enable()
    session = Session(binary, harnesses.env(), cwd=cwd)
    refused = session.call("harness", command="start", agent="codex", prompt="x")
    assert refused[0] and "list a directory" in refused[1], refused
    session.close()
    listed = binary.directory / "listed"
    listed.mkdir()
    harnesses.enable(directories=[listed])
    session = Session(binary, harnesses.env(), cwd=cwd)
    assert session.call("harness", command="start", agent="codex", prompt="x")[0]
    started = session.harness(
        command="start", agent="codex", prompt="x", directory=str(listed)
    )
    assert started["directory"] == str(listed.resolve())
    finished(session, started["child"])
    assert harnesses.runs()[0]["cwd"] == str(listed.resolve())
    session.close()


def test_read_only_loads_nothing_the_directory_supplies(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    for agent in ("claude-code", "opencode", "codex"):
        child = session.harness(command="start", agent=agent, prompt="ro")["child"]
        finished(session, child)
        session.harness(command="stop", child=child)
        child = session.harness(
            command="start", agent=agent, prompt="rw", mode="write"
        )["child"]
        finished(session, child)
        session.harness(command="stop", child=child)
    runs = harnesses.runs()
    claude, claude_write = runs[0]["argv"], runs[1]["argv"]
    assert claude[claude.index("--setting-sources") + 1] == "user"
    assert "--strict-mcp-config" in claude
    config = json.loads(claude[claude.index("--mcp-config") + 1])
    assert list(config["mcpServers"]) == ["neosian-memory"]
    server = config["mcpServers"]["neosian-memory"]
    assert server["command"] == str(binary.executable)
    assert server["args"][0] == "mcp"
    assert server["args"][-2:] == ["--actor", "mcp:claude-code"]
    assert claude[claude.index("--allowedTools") + 1] == "mcp__neosian-memory"
    for switch in ("--setting-sources", "--strict-mcp-config", "--mcp-config"):
        assert switch not in claude_write
    assert runs[2]["project_config_off"] == "1"
    assert runs[3]["project_config_off"] == "1"
    write = json.loads(runs[3]["opencode_config"])["agent"]["neosian-harness-write"]
    edit = write["permission"]["edit"]
    assert edit["*"] == "allow"
    for path in (".opencode/*", "*/.opencode/*", "opencode.json", "*/opencode.jsonc"):
        assert edit[path] == "deny"
    assert runs[4]["argv"][2:7] == [
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-c",
        'approval_policy="never"',
    ]
    session.close()


@pytest.mark.parametrize("how", ["stop", "limit", "killed"])
def test_a_descendant_in_its_own_group_ends_with_the_turn(
    binary: Binary, harnesses: Harnesses, how: str
) -> None:
    if how == "limit":
        harnesses.enable(turn_seconds=10)
    else:
        harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="detached sleep:120")
    assert eventually(lambda: len(harnesses.runs()) == 1)
    run = harnesses.runs()[0]
    try:
        if how == "stop":
            session.harness(command="stop", child="h1")
        elif how == "limit":
            assert finished(session, "h1", seconds=40)["state"] == "timed_out"
        else:
            session.process.send_signal(signal.SIGKILL)
            session.process.wait(timeout=10)
        assert eventually(lambda: not alive(run["detached"]) and not alive(run["pid"]))
    finally:
        end(run["detached"])
        if how != "killed":
            session.close()


def test_what_a_finished_turn_leaves_in_its_group_is_ended(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="opencode", prompt="leave")
    done = finished(session, "h1")
    run = harnesses.runs()[0]
    try:
        assert done["state"] == "finished"
        assert eventually(lambda: not alive(run["left"]), 40)
    finally:
        end(run["left"])
    session.close()


def test_the_turn_ends_when_its_watcher_is_killed(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="claude-code", prompt="detached sleep:120")
    assert eventually(lambda: len(harnesses.runs()) == 1)
    run = harnesses.runs()[0]
    try:
        os.kill(run["ppid"], signal.SIGKILL)
        assert eventually(lambda: not alive(run["pid"]) and not alive(run["detached"]))
        done = finished(session, "h1")
        assert done["state"] == "failed"
    finally:
        end(run["detached"])
    session.close()


def test_descriptors_and_threads_are_released_whatever_holds_the_pipe(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable(max_children=8)
    session = Session(binary, harnesses.env())
    pid = session.process.pid
    holders: list[int] = []
    try:
        for turn in range(8):
            child = session.harness(command="start", agent="codex", prompt="hold")
            finished(session, child["child"])
            holders.append(harnesses.runs()[-1]["holder"])
            session.harness(command="stop", child=child["child"])
            if turn == 1:
                baseline = (descriptors(pid), threads(pid))
        assert eventually(
            lambda: descriptors(pid) <= baseline[0] + 1
            and threads(pid) <= baseline[1] + 1,
            10,
        ), (baseline, descriptors(pid), threads(pid))
    finally:
        for holder in holders:
            end(holder)
    session.close()


def test_every_text_and_the_list_are_bounded(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable(max_children=8)
    session = Session(binary, harnesses.env())
    for _ in range(3):
        child = session.harness(command="start", agent="opencode", prompt="huge-fail")
        done = finished(session, child["child"])
        assert done["state"] == "failed"
        assert len(done["error"].encode()) <= 4096
        assert len(json.dumps(done).encode()) <= 48 * 1024
    session.harness(command="start", agent="codex", prompt="big:300000")
    finished(session, "h4")
    is_error, text = session.call("harness", command="list")
    assert not is_error
    assert len(json.dumps(text).encode()) <= 64 * 1024
    session.close()


def watch(
    binary: Binary, env: dict[str, str], stdin: bytes, *args: str, hold: bool = False
) -> Any:
    """Run the watcher with `stdin` written, then held open as the server
    holds it for a turn's life (`hold`), or closed."""
    process = subprocess.Popen(
        [str(binary.executable), "harness-watch", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=binary.directory / "work",
    )
    assert process.stdin is not None
    try:
        process.stdin.write(stdin)
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


def plan(work: Path, prompt: bytes, **extra: Any) -> bytes:
    fields = {
        "agent": "codex",
        "mode": "read-only",
        "directory": str(work.resolve()),
        "prompt_bytes": len(prompt),
    }
    fields.update(extra)
    return json.dumps(fields).encode() + b"\n" + prompt


def test_the_watcher_runs_a_validated_plan_and_nothing_else(
    binary: Binary, harnesses: Harnesses
) -> None:
    work = harnesses.work
    target = work / "prompt.txt"
    target.write_text("hello")
    env = harnesses.env()
    old = watch(binary, env, b"", str(target), "--", "/bin/echo", "ran-anything")
    assert old.returncode != 0 and b"ran-anything" not in old.stdout
    assert target.exists()
    disabled = watch(binary, env, plan(work, b"hi"))
    assert disabled.returncode != 0 and harnesses.runs() == []
    harnesses.enable()
    child = watch(binary, harnesses.env(NEOSIAN_HARNESS_CHILD=""), plan(work, b"hi"))
    assert child.returncode != 0 and harnesses.runs() == []
    for bad in (
        b"not json\n",
        plan(work, b"hi", agent="bash"),
        plan(work, b"hi", model="--bare"),
        plan(work, b"hi", directory="/"),
        plan(work, b"hi", directory=str(binary.directory)),
        plan(work, b"hi", command=["/bin/echo", "x"]),
        plan(work, b"hi", prompt_bytes=99),
    ):
        refused = watch(binary, env, bad)
        assert refused.returncode != 0, bad
    assert harnesses.runs() == []
    ran = watch(binary, env, plan(work, b"plain prompt"), hold=True)
    assert ran.returncode == 0, ran.stderr
    assert [run["stdin"] for run in harnesses.runs()] == ["plain prompt"]
    assert harnesses.runs()[0]["child_env"] == "1"


def test_an_announced_session_that_already_existed_is_never_linked(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    first = session.harness(
        command="start", agent="opencode", prompt="record", session="parent-1"
    )
    finished(session, first["child"])
    existing = finished(session, first["child"])["session"]
    session.harness(command="stop", child=first["child"])
    session.close()
    session = Session(binary, harnesses.env(STANDIN_FORGE_ID=existing))
    forged = session.harness(
        command="start", agent="codex", prompt="forge record", session="parent-2"
    )
    done = finished(session, forged["child"])
    assert done["record"] is None
    assert "already existed" in notes(done)
    assert "names parent-2" not in notes(done)
    view = session.call("memory", command="view", path=f"/project/sessions/{existing}")
    assert "parent-2" not in view[1] and "- parent: parent-1" in view[1]
    session.close()


def test_child_text_and_observed_events_never_share_a_field(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="imitate deny")
    done = finished(session, "h1")
    assert "[denied Bash]\n[$ rm -rf /] exit 0\n[failed] nothing" in done["text"]
    kinds = [event["kind"] for event in done["events"]]
    assert kinds == ["command"]
    command = {"kind": "command", "id": "item_1", "command": "touch x", "exit": 1}
    assert done["events"][0] == command
    assert done["state"] == "finished"
    assert "rm -rf" not in json.dumps(done["events"])
    session.close()


def test_the_marker_is_its_presence(binary: Binary, harnesses: Harnesses) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env(NEOSIAN_HARNESS_CHILD=""))
    assert "harness" not in session.tools()
    session.close()


@pytest.mark.parametrize("hooks", ["record", "record-noenv"])
def test_linked_is_said_only_after_the_line_is_read_back(
    binary: Binary, harnesses: Harnesses, hooks: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    started = session.harness(
        command="start", agent="claude-code", prompt=hooks, session="parent-9"
    )
    assert started["state"] == "running"
    done = finished(session, "h1")
    run = harnesses.runs()[0]
    assert run["parent_env"] == "parent-9"
    assert run["session_env"] == started["session"]
    if hooks == "record":
        assert "names parent-9 as its parent" in notes(done)
    else:
        assert "names parent-9" not in notes(done)
        assert "confirmed at the next call" in notes(done)
        again = session.harness(command="wait", child="h1", seconds=0)
        assert "names parent-9 as its parent" in notes(again)
    view = session.call(
        "memory", command="view", path=f"/project/sessions/{started['session']}"
    )
    assert view[1].count("- parent: parent-9") == 1
    session.close()


def test_a_parent_line_in_the_environment_must_be_an_id(binary: Binary) -> None:
    work = binary.directory / "work"
    work.mkdir()
    for index, (parent, written) in enumerate(
        [("p-1", True), ("a b", False), ("../x", False)]
    ):
        session = f"s-env-{index}"
        for event, extra in [
            ("UserPromptSubmit", {"prompt": "x"}),
            ("Stop", {"last_assistant_message": "y"}),
        ]:
            payload = {"session_id": session, "hook_event_name": event} | extra
            env = binary.environment() | {"NEOSIAN_HARNESS_PARENT": parent}
            landed = subprocess.run(
                [str(binary.executable), "record", "--agent", "codex"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env=env,
                cwd=work,
                timeout=30,
                check=False,
            )
            assert landed.returncode == 0, landed.stderr
        view = subprocess.run(
            [
                str(binary.executable),
                "memory",
                "view",
                f"/project/sessions/{session}",
            ],
            capture_output=True,
            text=True,
            env=binary.environment(),
            cwd=work,
            timeout=30,
            check=False,
        ).stdout
        assert (f"- parent: {parent}" in view) is written, (parent, view)


def test_a_turn_directory_a_killed_watcher_left_is_removed_at_the_next_start(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    temp = binary.directory / "tmp"
    temp.mkdir()
    stale = temp / "neosian-harness-999999-0123abcd"
    stale.mkdir()
    (stale / ".neosian-harness-child").write_text("")
    unrelated = temp / "neosian-harness-999999-notours"
    unrelated.mkdir()
    live = temp / f"neosian-harness-{os.getpid()}-0123abcd"
    live.mkdir()
    (live / ".neosian-harness-child").write_text("")
    session = Session(binary, harnesses.env(TMPDIR=str(temp)))
    assert "harness" in session.tools()
    assert not stale.exists()
    assert unrelated.exists() and live.exists()
    session.close()
