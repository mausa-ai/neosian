"""The harness tool over stdio, with stand-in harness CLIs built from the
real runs (record/x2b-captures-2026-11-21): absent unless enabled, never on
HTTP or in a child, each command's lifecycle and every bound."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.harness_support import Harnesses, Session, alive, eventually
from tests.contract.support import Binary, serve

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


def test_the_tool_is_absent_unless_enabled(
    binary: Binary, harnesses: Harnesses
) -> None:
    session = Session(binary, harnesses.env())
    assert "harness" not in session.tools()
    assert session.call("harness", command="list") == (True, "Tool 'harness' not found")
    assert session.close() == ""
    binary.home.mkdir(exist_ok=True)
    for text, warned in [
        ("[harness]\nenabled = false\n", False),
        ("[harness]\nenabled = 'yes'\n", True),
        ("[harness]\nenabled = true\nmax_children = 9\n", True),
        ("[harness]\nenabled = true\nturn_seconds = 5\n", True),
        ("[harness]\nenabled = true\nextra = 1\n", True),
    ]:
        (binary.home / "config.toml").write_text(text)
        session = Session(binary, harnesses.env())
        assert "harness" not in session.tools(), text
        stderr = session.close()
        assert ("harness tool off" in stderr) is warned, (text, stderr)
    harnesses.enable()
    session = Session(binary, harnesses.env())
    tool = session.tools()["harness"]
    assert tool["annotations"]["openWorldHint"] is True
    properties = tool["inputSchema"]["properties"]
    assert properties["agent"]["anyOf"][0]["enum"] == [
        "opencode",
        "codex",
        "claude-code",
    ]
    assert properties["mode"]["default"] == "read-only"
    assert tool["inputSchema"]["required"] == ["command"]
    assert session.close() == ""


def test_the_tool_is_never_served_over_http(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    from urllib.request import ProxyHandler, Request, build_opener

    with serve(binary) as server:
        opener = build_opener(ProxyHandler({}))
        headers = {
            "Authorization": f"Bearer {server.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        def post(body: dict[str, Any], extra: dict[str, str]) -> tuple[Any, str]:
            request = Request(
                server.url + "/mcp",
                data=json.dumps(body).encode(),
                headers=headers | extra,
                method="POST",
            )
            with opener.open(request, timeout=10) as response:
                text = response.read().decode()
                return response.headers, text

        opened, _ = post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {}
        )
        sid = {"mcp-session-id": opened["mcp-session-id"]}
        _, text = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
        data = json.loads(text.split("data: ", 1)[1].splitlines()[0])
        names = [tool["name"] for tool in data["result"]["tools"]]
        assert "memory" in names and "harness" not in names


@pytest.mark.parametrize("marker", ["env", "tmpdir"])
def test_a_child_server_never_has_the_tool(
    binary: Binary, harnesses: Harnesses, marker: str
) -> None:
    harnesses.enable()
    extra = {}
    if marker == "env":
        extra["NEOSIAN_HARNESS_CHILD"] = "1"
    else:
        private = binary.directory / "private"
        private.mkdir()
        (private / ".neosian-harness-child").write_text("")
        extra["TMPDIR"] = str(private)
    session = Session(binary, harnesses.env(**extra))
    assert "harness" not in session.tools()
    assert session.call("harness", command="start", agent="codex", prompt="x")[0]
    session.close()
    # The variable's presence is the marker, whatever its value (#430).
    session = Session(binary, harnesses.env(NEOSIAN_HARNESS_CHILD=""))
    assert "harness" not in session.tools()
    session.close()
    session = Session(binary, harnesses.env())
    assert "harness" in session.tools()
    session.close()


@pytest.mark.parametrize(
    "program,agent",
    [("claude", "claude-code"), ("codex", "codex"), ("opencode", "opencode")],
)
def test_start_wait_send_stop_and_list(
    binary: Binary, harnesses: Harnesses, program: str, agent: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    started = session.harness(command="start", agent=agent, prompt="first question")
    assert session.elapsed < 5
    assert started["child"] == "h1" and started["agent"] == agent
    assert started["mode"] == "read-only" and started["turn"] == 1
    assert started["directory"] == str(harnesses.work.resolve())
    done = finished(session, "h1")
    assert done["state"] == "finished", done
    assert done["answer"] == "echo: first question"
    assert "echo: first question" in done["text"]
    assert done["session"]
    assert done["record"] is None
    assert any("not recorded" in note for note in done["notes"])
    again = session.harness(command="wait", child="h1", seconds=0)
    assert again["text"] == "" and again["answer"] == "echo: first question"
    sent = session.harness(command="send", child="h1", prompt="second question")
    assert sent["turn"] == 2 and sent["session"] == done["session"]
    second = finished(session, "h1")
    assert second["answer"] == "echo: second question"
    listed = session.harness(command="list")
    assert [child["child"] for child in listed["children"]] == ["h1"]
    assert listed["children"][0]["state"] == "finished"
    assert listed["available"] == ["opencode", "codex", "claude-code"]
    assert listed["missing"] == []
    stopped = session.harness(command="stop", child="h1")
    assert stopped["state"] == "stopped"
    refused = session.call("harness", command="send", child="h1", prompt="third")
    assert refused == (True, "Child h1 was stopped; start a new child instead.")
    runs = harnesses.runs()
    assert [run["program"] for run in runs] == [program, program]
    assert [run["stdin"] for run in runs] == ["first question", "second question"]
    for run in runs:
        assert run["child_env"] == "1"
        assert not any("question" in word for word in run["argv"])
    if program == "claude":
        assert runs[0]["argv"][4:6] == ["--session-id", done["session"]]
        assert runs[1]["argv"][4:6] == ["--resume", done["session"]]
    elif program == "codex":
        assert runs[1]["argv"][-3:] == ["resume", done["session"], "-"]
    else:
        assert "--standalone" in runs[0]["argv"]
        assert runs[1]["argv"][-2:] == ["--session", done["session"]]
    assert session.close() == ""


def test_a_turn_outlives_wait_and_send_is_refused_meanwhile(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="slow sleep:4")
    waited = session.harness(command="wait", child="h1", seconds=1)
    assert waited["state"] == "running" and 0.8 < session.elapsed < 3
    assert "working" in waited["text"]
    assert "answer" not in waited
    assert any("still running" in note for note in waited["notes"])
    busy = session.call("harness", command="send", child="h1", prompt="more")
    assert busy == (
        True,
        "Child h1 is still running a turn; wait for it or stop it first.",
    )
    done = finished(session, "h1")
    assert done["state"] == "finished" and done["answer"].startswith("echo: slow")
    assert "working" not in done["text"]
    session.close()


def test_stop_ends_the_turn_and_everything_it_started(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(
        command="start", agent="claude-code", prompt="grandchild ignore-term sleep:60"
    )
    assert eventually(lambda: len(harnesses.runs()) == 1)
    run = harnesses.runs()[0]
    stopped = session.harness(command="stop", child="h1")
    assert session.elapsed < 10
    assert stopped["state"] == "stopped"
    assert not alive(run["pid"]) and not alive(run["grandchild"])
    session.close()


def test_a_turn_past_its_limit_is_stopped_and_reported(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable(turn_seconds=10)
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="opencode", prompt="grandchild sleep:60")
    assert eventually(lambda: len(harnesses.runs()) == 1)
    run = harnesses.runs()[0]
    done = finished(session, "h1", seconds=40)
    assert done["state"] == "timed_out"
    assert any("limit of 10 seconds" in note for note in done["notes"])
    assert not alive(run["pid"]) and not alive(run["grandchild"])
    session.close()


def test_children_are_bounded(binary: Binary, harnesses: Harnesses) -> None:
    harnesses.enable(max_children=1)
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="one")
    refused = session.call("harness", command="start", agent="codex", prompt="two")
    assert refused == (True, "At most 1 children may be open at once; stop one first.")
    finished(session, "h1")
    assert session.call("harness", command="start", agent="codex", prompt="two")[0]
    session.harness(command="stop", child="h1")
    assert (
        session.harness(command="start", agent="codex", prompt="two")["child"] == "h2"
    )
    session.close()


def test_output_is_bounded_with_the_cut_stated(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="claude-code", prompt="big:40000")
    done = finished(session, "h1")
    assert len(done["text"].encode()) <= 16384
    cut = done["events"][0]
    assert cut["kind"] == "truncated" and cut["field"] == "text"
    assert cut["bytes"] > 20000
    assert done["text"].endswith("echo: big:40000")
    assert any("earlier bytes of new output are not shown" in n for n in done["notes"])
    session.close()


def test_a_missing_harness_names_the_available_ones(binary: Binary) -> None:
    harnesses = Harnesses(binary, programs=("claude", "opencode")).install()
    harnesses.enable()
    session = Session(binary, harnesses.env())
    refused = session.call("harness", command="start", agent="codex", prompt="x")
    assert refused == (
        True,
        "codex is not installed on this server's PATH. Available: opencode, claude-code.",
    )
    listed = session.harness(command="list")
    assert listed["available"] == ["opencode", "claude-code"]
    assert listed["missing"] == ["codex"]
    session.close()


def test_invalid_inputs_are_refused_before_anything_starts(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    cases: list[tuple[dict[str, Any], str]] = [
        (
            {"command": "start", "agent": "cursor", "prompt": "x"},
            "Invalid arguments for tool 'harness'",
        ),
        ({"command": "start", "prompt": "x"}, "agent is required for start."),
        ({"command": "start", "agent": "codex"}, "prompt is required for start."),
        (
            {"command": "start", "agent": "codex", "prompt": ""},
            "prompt must be 1 byte to 256 KiB.",
        ),
        (
            {"command": "start", "agent": "codex", "prompt": "x" * 262145},
            "prompt must be",
        ),
        (
            {"command": "start", "agent": "codex", "prompt": "x", "model": "a b"},
            "model must be",
        ),
        (
            {"command": "start", "agent": "codex", "prompt": "x", "model": "$(id)"},
            "model must be",
        ),
        (
            {
                "command": "start",
                "agent": "claude-code",
                "prompt": "x",
                "model": "--bare",
            },
            "model must be",
        ),
        (
            {"command": "start", "agent": "codex", "prompt": "x", "directory": "nope"},
            "directory nope is not",
        ),
        (
            {"command": "start", "agent": "codex", "prompt": "x", "session": "a/b"},
            "session is not",
        ),
        (
            {"command": "start", "agent": "codex", "prompt": "x", "mode": "admin"},
            "Invalid arguments",
        ),
        ({"command": "wait", "child": "h9"}, "No child h9 in this session"),
        ({"command": "wait"}, "child is required for wait."),
        (
            {"command": "wait", "child": "h1", "seconds": 31},
            "seconds must be from 0 to 30.",
        ),
        ({"command": "stop", "child": "h1"}, "No child h1"),
    ]
    for arguments, expected in cases:
        is_error, text = session.call("harness", **arguments)
        assert is_error and str(text).startswith(expected), (arguments, text)
    assert harnesses.runs() == []
    session.close()


def test_a_failed_turn_reports_its_exit_and_error(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="opencode", prompt="fail")
    done = finished(session, "h1")
    assert done["state"] == "failed" and done["exit"] == 1
    assert done["error"] == "Insufficient balance."
    session.harness(command="start", agent="codex", prompt="deny")
    denied = finished(session, "h2")
    assert {"kind": "command", "command": "touch x", "exit": 1} in denied["events"]
    session.close()


def test_children_end_when_the_server_exits(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(
        command="start", agent="codex", prompt="grandchild ignore-term sleep:120"
    )
    assert eventually(lambda: len(harnesses.runs()) == 1)
    run = harnesses.runs()[0]
    started = time.monotonic()
    session.close(timeout=20)
    assert time.monotonic() - started < 15
    assert eventually(lambda: not alive(run["pid"]) and not alive(run["grandchild"]))


def test_children_end_when_the_server_is_killed(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(
        command="start", agent="claude-code", prompt="grandchild ignore-term sleep:120"
    )
    assert eventually(lambda: len(harnesses.runs()) == 1)
    run = harnesses.runs()[0]
    session.process.send_signal(signal.SIGKILL)
    session.process.wait(timeout=10)
    assert eventually(
        lambda: not alive(run["pid"]) and not alive(run["grandchild"]), 20
    )


def test_the_child_is_recorded_and_linked_to_its_parent(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    started = session.harness(
        command="start", agent="claude-code", prompt="record please", session="parent-7"
    )
    child = started["session"]
    done = finished(session, "h1")
    assert done["record"] == {"conversation": child, "turns": "1"}
    assert f'recall_turn(n, conversation="{child}")' in " ".join(done["notes"])
    assert any("names parent-7 as its parent" in note for note in done["notes"])
    path = f"/project/sessions/{child}"
    view = session.call("memory", command="view", path=path)[1]
    assert "- parent: parent-7" in view
    recalled = session.call("recall_turn", turn=1, conversation=child)[1]
    assert "record please" in recalled
    session.harness(command="send", child="h1", prompt="record again")
    second = finished(session, "h1")
    assert second["record"] == {"conversation": child, "turns": "2"}
    view = session.call("memory", command="view", path=path)[1]
    assert view.count("- parent: parent-7") == 1 and "- turns: 2" in view
    # A turn landed by the record verb alone keeps the line it did not write.
    for payload in (
        {"session_id": child, "hook_event_name": "UserPromptSubmit", "prompt": "p3"},
        {"session_id": child, "hook_event_name": "Stop", "last_assistant_message": "a"},
    ):
        landed = subprocess.run(
            [str(binary.executable), "record", "--agent", "claude-code"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=binary.environment(),
            cwd=harnesses.work,
            timeout=30,
            check=False,
        )
        assert landed.returncode == 0, landed.stderr
    view = session.call("memory", command="view", path=path)[1]
    assert view.count("- parent: parent-7") == 1 and "- turns: 3" in view
    session.harness(command="start", agent="codex", prompt="record without parent")
    unlinked = finished(session, "h2")
    assert unlinked["record"]["turns"] == "1"
    assert any("no session was given" in note for note in unlinked["notes"])
    session.close()


def test_every_child_denial_layer_reaches_the_child(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    for agent in ("claude-code", "opencode", "codex"):
        child = session.harness(command="start", agent=agent, prompt="probe")["child"]
        finished(session, child)
        session.harness(command="stop", child=child)
    runs = {run["program"]: run for run in harnesses.runs()}
    for run in runs.values():
        assert "harness" not in run["child_tools"] and "memory" in run["child_tools"]
    claude = runs["claude"]["argv"]
    assert claude[claude.index("--disallowedTools") + 1 :] == [
        "mcp__neosian-memory__harness",
        "mcp__neosian__harness",
    ]
    config = json.loads(runs["opencode"]["opencode_config"])
    for name in ("neosian-harness-read-only", "neosian-harness-write"):
        rules = config["agent"][name]["permission"]
        assert rules["neosian-memory_harness"] == rules["neosian_harness"] == "deny"
    codex = runs["codex"]["argv"]
    assert 'mcp_servers.neosian-memory.env.NEOSIAN_HARNESS_CHILD="1"' in codex
    assert 'mcp_servers.neosian-memory.disabled_tools=["harness"]' in codex
    assert not any("mcp_servers.neosian." in word for word in codex)
    assert Path(runs["codex"]["tmpdir"], ".neosian-harness-child").exists() is False
    session.close()


@pytest.mark.parametrize("key", ["", "my-memory"])
def test_codex_children_are_denied_without_a_known_key(
    binary: Binary, harnesses: Harnesses, key: str
) -> None:
    """No override names a key Codex lacks (it would refuse to start); the
    TMPDIR marker alone keeps the tool from a server under another key."""
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_CODEX_KEY=key))
    session.harness(command="start", agent="codex", prompt="probe")
    done = finished(session, "h1")
    assert done["state"] == "finished", done
    run = harnesses.runs()[0]
    neosian = ("mcp_servers.neosian-memory.", "mcp_servers.neosian.")
    assert not any(word.startswith(neosian) for word in run["argv"])
    assert "harness" not in run["child_tools"] and "memory" in run["child_tools"]
    session.close()


def test_codex_keeps_the_tools_a_key_already_disables(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    env = harnesses.env(STANDIN_CODEX_DISABLED='["upkeep_apply"]')
    session = Session(binary, env)
    session.harness(command="start", agent="codex", prompt="x", mode="write")
    finished(session, "h1")
    argv = harnesses.runs()[0]["argv"]
    assert (
        'mcp_servers.neosian-memory.disabled_tools=["upkeep_apply","harness"]' in argv
    )
    assert argv[2:4] == ["-s", "workspace-write"]
    assert "--skip-git-repo-check" not in argv
    session.close()


def test_a_child_in_another_directory_is_recorded_but_not_linked(
    binary: Binary, harnesses: Harnesses
) -> None:
    other = binary.directory / "elsewhere"
    other.mkdir()
    harnesses.enable(directories=[other])
    session = Session(binary, harnesses.env())
    session.harness(
        command="start",
        agent="opencode",
        prompt="record here",
        directory=str(other),
        session="parent-8",
    )
    done = finished(session, "h1")
    assert done["directory"] == str(other.resolve())
    assert harnesses.runs()[0]["cwd"] == str(other.resolve())
    assert done["record"]["turns"] == "1"
    assert any("not in a scope this server mounts" in note for note in done["notes"])
    session.close()


def test_a_session_id_that_reads_as_an_option_is_never_continued(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="bad-session")
    done = finished(session, "h1")
    assert done["state"] == "finished" and done["session"] is None
    refused = session.call("harness", command="send", child="h1", prompt="again")
    assert refused[0] and "has no harness session to continue" in refused[1]
    assert len(harnesses.runs()) == 1
    session.close()


@pytest.mark.parametrize(
    "agent,program,read_only,write",
    [
        (
            "claude-code",
            "claude",
            ["--permission-mode", "dontAsk", "--tools", "Read,Grep,Glob"],
            ["--permission-mode", "acceptEdits", "--permission-prompts", "none"],
        ),
        (
            "codex",
            "codex",
            ["-s", "read-only", "-c", 'approval_policy="never"'],
            ["-s", "workspace-write", "-c", 'approval_policy="never"'],
        ),
        (
            "opencode",
            "opencode",
            ["--agent", "neosian-harness-read-only"],
            ["--agent", "neosian-harness-write"],
        ),
    ],
)
def test_each_mode_reaches_the_harness_as_its_own_preset(
    binary: Binary,
    harnesses: Harnesses,
    agent: str,
    program: str,
    read_only: list[str],
    write: list[str],
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    for mode in ("read-only", "write"):
        child = session.harness(command="start", agent=agent, prompt="m", mode=mode)
        assert child["mode"] == mode
        finished(session, child["child"])
        session.harness(command="stop", child=child["child"])
    runs = harnesses.runs()
    for run, expected in zip(runs, (read_only, write), strict=True):
        argv = run["argv"]
        assert run["program"] == program
        start = argv.index(expected[0])
        assert argv[start : start + len(expected)] == expected
    session.close()


def test_children_run_side_by_side(binary: Binary, harnesses: Harnesses) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="opencode", prompt="café sleep:3")
    session.harness(command="start", agent="claude-code", prompt="thé sleep:3")
    states = [c["state"] for c in session.harness(command="list")["children"]]
    assert states == ["running", "running"]
    waited = session.harness(command="wait", child="h2", seconds=0)
    assert waited["state"] == "running" and session.elapsed < 1
    first, second = finished(session, "h1"), finished(session, "h2")
    assert first["answer"] == "echo: café sleep:3"
    assert second["answer"] == "echo: thé sleep:3"
    is_error, raw = session.call("harness", command="wait", child="h1", seconds=0)
    assert not is_error and raw["answer"] == "echo: café sleep:3"
    text = session.request(
        "tools/call",
        {"name": "harness", "arguments": {"command": "wait", "child": "h1"}},
    )["result"]["content"][0]["text"]
    assert "café" in text and "\\u00e9" not in text
    session.close()
