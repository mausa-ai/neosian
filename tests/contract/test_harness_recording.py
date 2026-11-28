"""The watcher records a child's turn itself (X4a, record/l1-plan-2026-11-27
section B): from the harness's native output, through the record verb, so
the recorder's rules hold for it as for a hook's turn. The stand-in
harnesses print what the real ones printed (record/x2b-captures-2026-11-21);
the child's own hooks are absent, refused, suppressed for the watched
session, or unaware of the harness. The parent reads the turn and its
lineage back through its own MCP server; HTTP destinations are a real
`neosian serve` behind the landing contracts' forwarding stub."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.contract.harness_support import Harnesses, Session
from tests.contract.landing.conftest import slots_held
from tests.contract.landing.lander import (
    enable,
    environment,
    land,
    lander,
    pending,
    retry,
    served_root,
    turns,
)
from tests.contract.landing.stub import Rule, forwarding
from tests.contract.profile_support import bind, install
from tests.contract.support import ADMIN, Binary, Server
from tests.contract.test_profile_capture import capture_profile

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the tool is absent on Windows")

AGENTS = [("claude", "claude-code"), ("codex", "codex"), ("opencode", "opencode")]
NAMES = [agent for _, agent in AGENTS]
WRITE = "conversation/write_records"


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


def recorded(session: Session, child: str, turns: str, seconds: int = 30) -> Any:
    """The child once the parent reads `turns` of its conversation."""
    deadline = time.monotonic() + seconds
    while True:
        answer = session.harness(command="wait", child=child, seconds=0)
        found = answer["record"] and answer["record"]["turns"]
        if answer["state"] != "running" and found:
            assert found == turns, answer
            return answer
        assert time.monotonic() < deadline, answer
        time.sleep(0.1)


def stored(root: Path, conversation: str) -> list[Any]:
    """The conversation's turns as the FileStore at `root` holds them."""
    path = root / "conversations" / conversation / "turns.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def hooks(harnesses: Harnesses) -> list[Any]:
    """What the child's own hooks were answered, in order."""
    return [entry for entry in harnesses.entries() if "hook" in entry]


@contextmanager
def served(binary: Binary) -> Iterator[Server]:
    """`neosian serve` over a root that keeps records, as the landing
    contracts serve it."""
    enable(binary, served_root(binary))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    env = {**binary.environment(), "NEOSIAN_SERVE_TOKEN": f"client:contract={ADMIN}"}
    process = subprocess.Popen(
        [str(binary.executable), "serve", "--port", str(port)]
        + ["--root", str(served_root(binary))],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
        cwd=binary.directory,
    )
    server = Server(f"http://127.0.0.1:{port}")
    try:
        deadline = time.monotonic() + 20
        while True:
            assert process.poll() is None, process.communicate()[1]
            try:
                if server.request("/health", method="GET", authenticated=False):
                    break
            except OSError:
                pass
            assert time.monotonic() < deadline, "serve did not become healthy"
            time.sleep(0.02)
        yield server
    finally:
        process.terminate()
        process.communicate(timeout=10)


@pytest.mark.parametrize("mode", ["read-only", "write"])
@pytest.mark.parametrize("agent", NAMES)
def test_a_large_result_is_read_back_with_the_hooks_unable_to_file(
    binary: Binary, harnesses: Harnesses, agent: str, mode: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    prompt = "tool:300000 record-refused"
    session.harness(
        command="start", agent=agent, mode=mode, prompt=prompt, session="parent-1"
    )
    done = finished(session, "h1")
    assert done["state"] == "finished", done
    # The child's hooks ran where they could write nothing, as in a sandbox.
    answered = hooks(harnesses)
    assert [entry["hook"] for entry in answered] == ["UserPromptSubmit", "Stop"]
    assert all(json.loads(entry["answer"])["error"] for entry in answered)
    conversation = done["conversation"]
    if done["record"] is None:
        # Past 128 KiB the turn waits for the lander its Stop started.
        assert "waits in the record queue" in notes(done)
        assert "Linked" not in notes(done)
    landed = recorded(session, "h1", "1")
    assert "names parent-1 as its parent" in notes(landed)
    recalled = session.call("recall_turn", turn=1, conversation=conversation)[1]
    assert prompt in recalled
    [turn] = stored(binary.home, conversation)
    texts = [m.get("content") or "" for m in turn["messages"]]
    result = ("head:" + "r" * 300000 + ":tail")[:300000]
    assert texts[0] == prompt
    assert any(result in text for text in texts), "the result is whole"
    assert turn["messages"][-1]["content"] == "echo: tool:300000 record-refused"
    view = session.call(
        "memory", command="view", path=f"/project/sessions/{conversation}"
    )
    assert view[1].count("- parent: parent-1") == 1
    session.close()


@pytest.mark.parametrize("program,agent", AGENTS)
def test_stdout_past_the_display_is_cut_there_and_recorded_whole(
    binary: Binary, harnesses: Harnesses, program: str, agent: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent=agent, prompt="big:300000 big-answer:200000")
    done = finished(session, "h1")
    assert done["state"] == "finished"
    assert "earlier bytes of new output are not shown" in notes(done)
    assert len(done["answer"]) == 16 * 1024 and done["answer_bytes"] == 200000
    recorded(session, "h1", "1")
    [turn] = stored(binary.home, done["conversation"])
    answer = turn["messages"][-1]["content"]
    assert answer.endswith("a" * 200000)
    if program != "opencode":
        assert answer == "a" * 200000
    else:
        # OpenCode's recorder keeps the newest step's words, joined.
        assert answer.count("b" * 999) == 300
    session.close()


def test_capture_off_and_a_paused_conversation_record_nothing(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    install(binary, capture_profile("local"))
    bind(binary, "--root", str(binary.directory / "knowledge"))
    history = binary.directory / "history"
    bind(binary, "--root", str(history), "--capture", "local", source="history")
    install(binary, capture_profile("off"))
    env = harnesses.env(NEOSIAN_PROFILE="portable")
    session = Session(binary, env)
    session.harness(command="start", agent="codex", prompt="capture is off")
    off = finished(session, "h1")
    assert off["state"] == "finished" and off["record"] is None
    assert "capture is off for this profile" in notes(off)
    assert not (history / "conversations").exists()
    assert not (binary.home / "conversations").exists()
    session.close()
    install(binary, capture_profile("local"))
    session = Session(binary, env)
    session.harness(command="start", agent="claude-code", prompt="first, recorded")
    first = recorded(session, "h1", "1")
    conversation = first["conversation"]
    assert conversation.startswith("w-")
    paused = binary.json("record", "pause", conversation)
    assert paused["disposition"] == "capture_paused"
    session.harness(command="send", child="h1", prompt="second, while paused")
    second = finished(session, "h1")
    assert second["state"] == "finished" and second["record"] is None
    assert "recording is paused for this conversation" in notes(second)
    assert [t["messages"][0]["content"] for t in stored(history, conversation)] == [
        "first, recorded"
    ]
    session.close()


def test_a_lost_receipt_records_the_turn_once(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    with served(binary) as server, forwarding(server.url) as stub:
        env = harnesses.env(NEOSIAN_CLIENT_TOKEN=ADMIN)
        session = Session(binary, env, args=("--url", stub.url))
        # The turn waits in the queue; the test is the lander (ruling D11).
        with slots_held(binary.home):
            session.harness(command="start", agent="codex", prompt="lost receipt")
            done = finished(session, "h1")
        assert done["record"] is None and "waits in the record queue" in notes(done)
        conversation = done["conversation"]
        assert pending(binary, conversation) == [1]
        stub.add(Rule(when=lambda e: e.route == WRITE, kind="drop"))
        first = land(binary)
        assert first["landed"] == 0
        assert stub.records(conversation)[0].forwarded, "the server took it"
        retry(binary)
        stub.next_run()
        second = land(binary)
        assert second["landed"] == 1
        assert len(turns(server, conversation)) == 1
        assert recorded(session, "h1", "1")["record"]["conversation"] == conversation
        session.close()


def test_a_turn_closed_for_one_destination_is_never_sent_under_another(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    with served(binary) as server, forwarding(server.url) as stub:
        env = harnesses.env(NEOSIAN_CLIENT_TOKEN=ADMIN)
        session = Session(binary, env, args=("--url", stub.url))
        with slots_held(binary.home):
            session.harness(command="start", agent="opencode", prompt="one account")
            done = finished(session, "h1")
        conversation = done["conversation"]
        # Another account's token on the same URL: the turn is left queued.
        other = subprocess.run(
            [str(binary.executable), "record-land", "--home", str(binary.home)],
            env={**environment(binary), "NEOSIAN_CLIENT_TOKEN": "someone-else"},
            cwd=binary.directory,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert other.returncode == 0, other.stderr
        assert stub.records() == []
        assert pending(binary, conversation) == [1]
        assert land(binary)["landed"] == 1
        sent = stub.records(conversation)
        assert len({exchange.body["draft"] for exchange in sent}) == 1
        assert len(turns(server, conversation)) == 1
        assert lander(binary).returncode == 0 and stub.records(conversation) == sent
        session.close()


@pytest.mark.parametrize("agent", NAMES)
def test_a_resumed_child_keeps_its_lineage(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent=agent, prompt="first", session="parent-4")
    first = recorded(session, "h1", "1")
    conversation = first["conversation"]
    assert "names parent-4 as its parent" in notes(first)
    session.harness(command="send", child="h1", prompt="second")
    second = recorded(session, "h1", "2")
    assert second["conversation"] == conversation
    assert "names parent-4 as its parent" in notes(second)
    recalled = session.call("recall_turn", turn=2, conversation=conversation)[1]
    assert "second" in recalled
    view = session.call(
        "memory", command="view", path=f"/project/sessions/{conversation}"
    )
    assert view[1].count("- parent: parent-4") == 1 and "- turns: 2" in view[1]
    session.close()


@pytest.mark.parametrize("agent", NAMES)
def test_hooks_that_also_file_leave_one_record(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent=agent, prompt="record", session="parent-5")
    first = recorded(session, "h1", "1")
    session.harness(command="send", child="h1", prompt="record twice tool:64")
    recorded(session, "h1", "2")
    answered = [
        json.loads(entry["answer"])["disposition"] for entry in hooks(harnesses)
    ]
    assert (
        answered == ["watched"] * 4
    ), "the hooks spool nothing for the watched session"
    kept = stored(binary.home, first["conversation"])
    assert [t["messages"][0]["content"] for t in kept] == [
        "record",
        "record twice tool:64",
    ]
    calls = [m for m in kept[1]["messages"] if m.get("tool_calls")]
    assert len(calls) == 1, "a tool call printed twice is one call"
    # A hook that knows nothing of the harness lands a new session itself;
    # the watcher does not file it again.
    session.harness(command="start", agent=agent, prompt="record-bare")
    bare = recorded(session, "h2", "1")
    assert len(stored(binary.home, bare["conversation"])) == 1
    session.close()


@pytest.mark.parametrize("agent", NAMES)
def test_a_turn_the_provider_did_not_complete_is_reported_not_made_up(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    started = session.harness(command="start", agent=agent, prompt="tool:64 fail")
    done = finished(session, "h1")
    assert done["state"] == "failed" and done["record"] is None
    assert "the harness did not complete it" in notes(done)
    native = done["session"] or started["session"]
    assert stored(binary.home, native) == []
    session.close()


def test_an_existing_session_is_never_adopted(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="opencode", prompt="the person's own")
    existing = recorded(session, "h1", "1")["conversation"]
    session.close()
    session = Session(binary, harnesses.env(STANDIN_FORGE_ID=existing))
    session.harness(command="start", agent="codex", prompt="forge", session="parent-6")
    done = finished(session, "h1")
    assert done["state"] == "finished" and done["record"] is None
    assert "already existed" in notes(done)
    kept = stored(binary.home, existing)
    assert [t["messages"][0]["content"] for t in kept] == ["the person's own"]
    session.close()


def test_a_lineage_is_claimed_only_once_read_back(
    binary: Binary, harnesses: Harnesses
) -> None:
    other = binary.directory / "elsewhere"
    other.mkdir()
    harnesses.enable(directories=[other])
    # A store that keeps records lands no turn inline (ruling E12 of the
    # T1e1 plan); with both landing slots held the turn waits.
    enable(binary, binary.home)
    session = Session(binary, harnesses.env())
    with slots_held(binary.home):
        session.harness(command="start", agent="codex", prompt="queued", session="p-7")
        queued = finished(session, "h1")
        assert queued["record"] is None
        assert "waits in the record queue" in notes(queued)
        assert "Linked" not in notes(queued)
        assert "does not exist yet" in notes(queued)
    assert land(binary)["landed"] == 1
    landed = recorded(session, "h1", "1")
    assert "names p-7 as its parent" in notes(landed)
    # Filed under another project's scope: recorded, and never said linked.
    session.harness(command="stop", child="h1")
    session.harness(
        command="start",
        agent="claude-code",
        prompt="there",
        directory=str(other),
        session="p-8",
    )
    elsewhere = recorded(session, "h2", "1")
    assert "not in a scope this server mounts" in notes(elsewhere)
    assert "Linked" not in notes(elsewhere)
    session.close()
