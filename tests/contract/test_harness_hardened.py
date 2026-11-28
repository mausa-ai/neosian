"""The hardening after the X4 phase review (X4e, rulings P1 to P8): the
watcher files under the parent's own store selection and protects that
store, no child holds the store's credential, a session that already had
turns is never continued, admission comes before any client command, and a
stop before filing prevents it. Every write lands inside pytest's
temporary directory."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.contract.harness_support import CONFINED, Harnesses, Session, alive, end
from tests.contract.profile_support import install
from tests.contract.support import ADMIN, Binary
from tests.contract.test_harness_recording import notes, recorded, served, stored
from tests.contract.test_harness_rereview import finished, plan, watch
from tests.contract.test_profile_capture import capture_profile

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the tool is absent on Windows")
macos = pytest.mark.skipif(not CONFINED, reason="the common policy is macOS only")


@pytest.fixture
def harnesses(binary: Binary) -> Harnesses:
    return Harnesses(binary).install()


def bind(binary: Binary, name: str, *args: str) -> None:
    done = binary.run("profile", "bind", name, *args, "--write")
    assert done.returncode == 0, done.stderr


def profiles(binary: Binary, bound: dict[str, Path]) -> None:
    """One profile per name, each with stores of its own, capture local, and
    bound to its directory."""
    for name, project in bound.items():
        install(binary, capture_profile("local"), name=name)
        knowledge = binary.directory / f"knowledge-{name}"
        bind(binary, name, "--source", "knowledge", "--root", str(knowledge))
        history = binary.directory / f"history-{name}"
        bind(
            binary,
            name,
            "--source",
            "history",
            "--root",
            str(history),
            "--capture",
            "local",
        )
        bind(binary, name, "--project", str(project))


def test_the_turn_is_filed_under_the_parents_profile_not_the_childs_directory(
    binary: Binary, harnesses: Harnesses
) -> None:
    """P1 (a): the server's directory selects profile a, capture off; the
    child works in a directory bound to profile b, capture on. The turn is
    not filed under b."""
    other = binary.directory / "elsewhere"
    other.mkdir()
    harnesses.enable(directories=[other])
    profiles(binary, {"a": harnesses.work, "b": other})
    install(binary, capture_profile("off"), name="a")
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="x", directory=str(other))
    done = finished(session, "h1")
    session.close()
    assert done["state"] == "finished" and done["record"] is None, done
    assert "capture is off for this profile" in notes(done)
    assert not (binary.directory / "history-b" / "conversations").exists()


@macos
def test_the_parents_file_store_is_protected_whatever_the_childs_directory_selects(
    binary: Binary, harnesses: Harnesses
) -> None:
    """P1 (a): a write child works where the parent's file store lies, in a
    directory bound to a profile; the store stays unwritable."""
    other = binary.directory / "elsewhere"
    (other / "store").mkdir(parents=True)
    harnesses.enable(directories=[other])
    profiles(binary, {"b": other})
    probe = other / "store" / "probe.txt"
    env = harnesses.env(STANDIN_WRITES=json.dumps({"store": str(probe)}))
    session = Session(binary, env, args=("--root", str(other / "store")))
    session.harness(
        command="start",
        agent="codex",
        prompt="writes",
        mode="write",
        directory=str(other),
    )
    finished(session, "h1")
    session.close()
    assert harnesses.runs()[0]["writes"] == {"store": "EPERM"}
    assert not probe.exists()


@macos
@pytest.mark.parametrize("mode", ["read-only", "write"])
def test_no_child_holds_the_stores_credential_and_says_it_has_no_memory_of_its_own(
    binary: Binary, harnesses: Harnesses, mode: str
) -> None:
    """P1 (b): a child of a store reached with a credential starts in either
    mode, without the credential and unable to read neosian's login state;
    its own neosian server fails at once, readably."""
    harnesses.enable()
    login = binary.home / "login" / "credential.toml"
    login.parent.mkdir(parents=True, mode=0o700)
    login.write_text("token = 'fixture'\n", encoding="utf-8")
    login.chmod(0o600)
    words = "reads probe-own" if mode == "read-only" else "reads"
    env = harnesses.env(
        NEOSIAN_CLIENT_TOKEN=ADMIN,
        NEOSIAN_SERVE_TOKEN=f"client:contract={ADMIN}",
        STANDIN_READS=json.dumps({"login": str(login)}),
    )
    with served(binary) as server:
        session = Session(binary, env, args=("--url", server.url))
        child = session.harness(
            command="start", agent="claude-code", prompt=words, mode=mode
        )
        done = finished(session, child["child"])
        session.close()
    assert done["state"] == "finished", done
    assert "has no neosian memory of its own" in notes(done)
    run = harnesses.runs()[0]
    assert run["store_token"] is False and run["reads"] == {"login": "EPERM"}
    assert run["serve_token"] is False, "a serving token is a bearer too (X4f Q2)"
    if mode == "read-only":
        own = run["own_server"]
        assert own["code"] != 0 and not own["answered"] and own["error"], own
        assert own["seconds"] < 10


def test_a_session_that_already_had_turns_is_never_continued(
    binary: Binary, harnesses: Harnesses
) -> None:
    """P2: the harness announced a session this store already holds; the
    watcher did not file it, and `send` does not continue it either."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="opencode", prompt="the person's own")
    existing = recorded(session, "h1", "1")["conversation"]
    session.close()
    session = Session(binary, harnesses.env(STANDIN_FORGE_ID=existing))
    session.harness(command="start", agent="codex", prompt="forge")
    assert finished(session, "h1")["record"] is None
    refused = session.call("harness", command="send", child="h1", prompt="append")
    assert refused[0] and "does not continue it" in refused[1], refused
    session.close()
    kept = stored(binary.home, existing)
    assert [t["messages"][0]["content"] for t in kept] == ["the person's own"]
    assert len(harnesses.runs()) == 2


@macos
def test_client_metadata_commands_run_like_the_turn_and_sign_in_outside_it(
    binary: Binary, harnesses: Harnesses
) -> None:
    """P4: Codex's `mcp get` and `mcp list` run under the prepared policy
    (its private TMPDIR) without the store credential; its sign-in status
    runs outside the policy, without the credential too."""
    harnesses.enable()
    session = Session(binary, harnesses.env(NEOSIAN_CLIENT_TOKEN="t0ken"))
    session.harness(command="start", agent="codex", prompt="x")
    assert finished(session, "h1")["state"] == "finished"
    session.close()
    entries = harnesses.entries()
    metadata = [e for e in entries if "mcp_get" in e or "mcp_list" in e]
    status = [e for e in entries if "status" in e and "argv" not in e]
    assert metadata and status
    for entry in metadata + status + harnesses.runs():
        assert entry["store_token"] is False, entry
    for entry in metadata:
        assert Path(entry["metadata_tmpdir"]).name.startswith("neosian-harness-"), entry
    for entry in status:
        assert not Path(str(entry["metadata_tmpdir"])).name.startswith(
            "neosian-harness-"
        )


def test_a_stop_before_filing_begins_prevents_the_filing(
    binary: Binary, harnesses: Harnesses
) -> None:
    """P6: the harness completed its turn, but a process it left holds its
    output open, so the watcher waits; a stop then reaches it before filing,
    and nothing is recorded."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    started = session.harness(command="start", agent="codex", prompt="hold")
    run: dict[str, Any] = {}
    deadline = time.monotonic() + 20
    while not run:
        assert time.monotonic() < deadline
        run = next(iter(harnesses.runs()), {})
    try:
        while alive(run["pid"]):
            assert time.monotonic() < deadline
            time.sleep(0.005)
        stopped = session.harness(command="stop", child=started["child"])
    finally:
        end(run["holder"])
    session.close()
    assert stopped["state"] == "stopped" and stopped["record"] is None, stopped
    assert "stopped before its watcher began to record it" in notes(stopped)
    assert not (binary.home / "conversations").exists()


# The second review (X4f, rulings Q1 to Q10).


def test_a_profile_rebound_while_the_server_runs_refuses_the_child(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Q1: the server resolved profile a with one history store; a is then
    bound to another. The child is refused, never filed in the new store."""
    harnesses.enable()
    profiles(binary, {"a": harnesses.work})
    session = Session(binary, harnesses.env())
    moved = binary.directory / "history-moved"
    bind(
        binary,
        "a",
        "--source",
        "history",
        "--root",
        str(moved),
        "--capture",
        "local",
        "--replace",
    )
    refused = session.call("harness", command="start", agent="codex", prompt="x")
    session.close()
    assert (
        refused[0] and "names other stores than when this server started" in refused[1]
    )
    assert harnesses.runs() == [] and not (moved / "conversations").exists()


def test_a_session_another_child_filed_first_is_not_continued(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Q3: two children announce one new session; the first files it, the
    second fails before filing. The second never claimed it: no `send`."""
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_FORGE_ID=str(uuid.uuid4())))
    session.harness(command="start", agent="codex", prompt="forge sleep:1")
    session.harness(command="start", agent="codex", prompt="forge fail sleep:3")
    assert finished(session, "h1")["state"] == "finished"
    assert finished(session, "h2")["state"] == "failed"
    refused = session.call("harness", command="send", child="h2", prompt="append")
    assert refused[0] and "does not continue it" in refused[1], refused
    session.close()
    assert len(harnesses.runs()) == 2


def test_a_stop_says_recorded_only_from_the_receipt(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Q4: with capture off a stop does not say the turn was recorded."""
    harnesses.enable()
    profiles(binary, {"a": harnesses.work})
    install(binary, capture_profile("off"), name="a")
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="x")
    assert finished(session, "h1")["state"] == "finished"
    stopped = session.harness(command="stop", child="h1")
    assert "capture is off" in notes(stopped) and "did not cut it" not in notes(stopped)
    session.close()


def test_a_process_holding_the_stores_lock_never_stalls_an_answer(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Q4: the answer's read of the store is bounded."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="opencode", prompt="y")
    finished(session, "h1")
    with (binary.home / ".store.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        answer = session.harness(command="wait", child="h1", seconds=0)
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert session.elapsed < 10 and "did not answer within 3 seconds" in notes(answer)
    session.close()


@macos
def test_a_client_the_child_could_replace_is_refused_and_one_file_serves_every_run(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Q5: the client found on a PATH entry inside the child's directory is
    refused in `write`; the watcher runs only the file the server probed."""
    harnesses.enable()
    inside = harnesses.work / "bin"
    inside.mkdir()
    shutil.copy2(harnesses.bin / "codex", inside / "codex")
    env = harnesses.env()
    env["PATH"] = os.pathsep.join([str(inside), env["PATH"]])
    session = Session(binary, env)
    refused = session.call(
        "harness", command="start", agent="codex", prompt="x", mode="write"
    )
    assert refused[0] and "lies where the child may write" in refused[1], refused
    child = session.harness(command="start", agent="codex", prompt="x")
    assert finished(session, child["child"])["state"] == "finished"
    session.close()
    other = binary.directory / "elsewhere" / "codex"
    other.parent.mkdir()
    shutil.copy2(harnesses.bin / "codex", other)
    direct = watch(binary, harnesses.env(), plan(harnesses.work, program=str(other)))
    assert direct.returncode == 2 and b"program" in direct.stderr


def test_a_metadata_helper_holding_the_output_never_holds_the_start(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Q6: Codex's `mcp get` leaves a helper holding its output; the start
    still returns at once and the helper is ended."""
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_MCP_HOLD="1"))
    session.harness(command="start", agent="codex", prompt="x", mode="write")
    assert session.elapsed < 10, session.elapsed
    finished(session, "h1")
    session.close()
    holders = [e["mcp_holder"] for e in harnesses.entries() if "mcp_holder" in e]
    try:
        assert holders and all(not alive(pid) for pid in holders)
    finally:
        for pid in holders:
            end(pid)


def test_a_neosian_registration_under_another_name_gets_the_marker_and_the_filter(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Q8: a stdio entry that runs neosian, named `my-memory`."""
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_CODEX_KEY="my-memory"))
    session.harness(command="start", agent="codex", prompt="x", mode="write")
    finished(session, "h1")
    session.close()
    argv = harnesses.runs()[0]["argv"]
    assert 'mcp_servers.my-memory.env.NEOSIAN_HARNESS_CHILD="1"' in argv
    assert 'mcp_servers.my-memory.disabled_tools=["harness"]' in argv
