"""The hardening after the X4 phase review (X4e, rulings P1 to P8): the
watcher files under the parent's own store selection and protects that
store, no child holds the store's credential, a session that already had
turns is never continued, admission comes before any client command, and a
stop before filing prevents it. Every write lands inside pytest's
temporary directory."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.harness_support import CONFINED, Harnesses, Session, alive, end
from tests.contract.profile_support import install
from tests.contract.support import ADMIN, Binary
from tests.contract.test_harness_recording import notes, recorded, served, stored
from tests.contract.test_harness_rereview import finished
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
        NEOSIAN_CLIENT_TOKEN=ADMIN, STANDIN_READS=json.dumps({"login": str(login)})
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
