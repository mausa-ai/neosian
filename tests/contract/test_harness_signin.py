"""A child's sign-in and Codex registrations by transport (X4c): the
client's own status command before a turn, outside the sandbox; one
instruction to sign in for a signed-out client and for a sign-in failure
during a turn; and a Codex child that starts with neosian registered over
HTTP or stdio, in both modes, with only the fields Codex accepts for each
transport. Everything here runs against stand-ins built from the clients'
own code and earlier captures; no real client or account is used."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from tests.contract.harness_support import Harnesses, Session, alive, eventually
from tests.contract.support import Binary
from tests.contract.test_harness_rereview import finished, plan, watch

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the tool is absent on Windows")

SIGN_IN = {
    "claude-code": "claude auth login",
    "codex": "codex login",
    "opencode": "opencode auth login",
}
# What the stand-ins print that neosian must never show.
SECRETS = (
    "SECRET",
    "person@example.com",
    "someone",
    "standinKEYTAIL",
    "standin-http-s3cret",
    "0a1b2c3d",
)


@pytest.fixture
def harnesses(binary: Binary) -> Harnesses:
    return Harnesses(binary).install()


def statuses(harnesses: Harnesses) -> list[dict[str, Any]]:
    return [entry for entry in harnesses.entries() if "status" in entry]


def clean(answer: Any) -> None:
    text = json.dumps(answer)
    for secret in SECRETS:
        assert secret not in text, (secret, text)


@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_a_signed_out_client_gets_the_instruction_and_no_child(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    """The status command runs outside the child, in its directory; a plain
    "not signed in" refuses the start with how to sign in, and no harness
    turn runs."""
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_AUTH="out"))
    refused = session.call("harness", command="start", agent=agent, prompt="x")
    assert refused[0], refused
    assert SIGN_IN[agent] in refused[1] and "outside the child" in refused[1]
    assert harnesses.runs() == []
    [status] = statuses(harnesses)
    assert status["child_env"] is None
    assert status["cwd"] == str(harnesses.work.resolve())
    clean(refused)
    session.close()


def test_a_status_command_that_does_not_answer_lets_the_child_start(
    binary: Binary, harnesses: Harnesses
) -> None:
    """A slow status command is not a signed-out one: after its bound the
    child starts with a note, and the command and its helper are gone."""
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_AUTH="slow"))
    started = time.monotonic()
    child = session.harness(command="start", agent="claude-code", prompt="x")
    assert 5 <= time.monotonic() - started < 20
    note = " ".join(child["notes"])
    assert "could not tell whether claude-code is signed in" in note
    assert "claude auth status: no answer within 5 seconds" in note
    assert finished(session, child["child"])["state"] == "finished"
    [status] = statuses(harnesses)
    for pid in (status["pid"], status["helper"]):
        assert eventually(lambda pid=pid: not alive(pid), 5), pid
    session.close()


@pytest.mark.parametrize("answer", ["malformed", "error", "network"])
@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_an_unknown_status_answer_is_never_taken_for_signed_out(
    binary: Binary, harnesses: Harnesses, agent: str, answer: str
) -> None:
    """A malformed answer, an error and a network failure are reported as
    what they are, with nothing of a credential shown, and the child starts."""
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_AUTH=answer))
    child = session.harness(command="start", agent=agent, prompt="x")
    note = " ".join(child["notes"])
    assert f"could not tell whether {agent} is signed in" in note, note
    assert "exit status 1" in note
    done = finished(session, child["child"])
    assert done["state"] == "finished"
    clean([child, done])
    assert len(harnesses.runs()) == 1
    session.close()


@pytest.mark.parametrize(("agent", "answer"), [("claude-code", "in"), ("codex", "key")])
def test_a_signed_in_status_shows_nothing_of_its_answer(
    binary: Binary, harnesses: Harnesses, agent: str, answer: str
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_AUTH=answer))
    child = session.harness(command="start", agent=agent, prompt="x")
    assert not any("signed in" in note for note in child["notes"])
    clean([child, finished(session, child["child"])])
    session.close()


def test_opencode_has_no_status_command_to_run(
    binary: Binary, harnesses: Harnesses
) -> None:
    harnesses.enable()
    session = Session(binary, harnesses.env(STANDIN_AUTH="out"))
    child = session.harness(command="start", agent="opencode", prompt="x")
    assert finished(session, child["child"])["state"] == "finished"
    assert statuses(harnesses) == []
    session.close()


@pytest.mark.parametrize("agent", ["claude-code", "codex", "opencode"])
def test_a_sign_in_failure_during_a_turn_is_the_same_instruction(
    binary: Binary, harnesses: Harnesses, agent: str
) -> None:
    """The client's own structured sign-in error, mid-turn, becomes the
    sentence a signed-out client gets before a turn, not the raw error, and
    a later raw error does not replace it."""
    harnesses.enable()
    expected = None
    if agent != "opencode":
        before = Session(binary, harnesses.env(STANDIN_AUTH="out"))
        expected = before.call("harness", command="start", agent=agent, prompt="x")[1]
        before.close()
    session = Session(binary, harnesses.env())
    child = session.harness(command="start", agent=agent, prompt="signin-fail")
    done = finished(session, child["child"])
    assert done["state"] == "failed", done
    assert SIGN_IN[agent] in done["error"] and "outside the child" in done["error"]
    if expected is not None:
        assert done["error"] == expected
    assert "standin-raw-error" not in done["error"]
    assert [e for e in done["events"] if e["kind"] == "failure"] == [
        {"kind": "failure"}
    ]
    session.close()


def overrides(argv: list[str]) -> list[str]:
    pairs = zip(argv, argv[1:], strict=False)
    return [
        value
        for flag, value in pairs
        if flag == "-c" and value.startswith("mcp_servers.")
    ]


def expected(entries: dict[str, str], mode: str) -> list[str]:
    """The fields each of neosian's keys gets, by transport (transport.rs)."""
    fields = []
    for key, kind in entries.items():
        ours = key in ("neosian-memory", "neosian") and kind == "stdio"
        if mode == "read-only" and not ours:
            fields.append(f"mcp_servers.{key}.enabled=false")
            continue
        if kind == "stdio":
            fields.append(f'mcp_servers.{key}.env.NEOSIAN_HARNESS_CHILD="1"')
        fields.append(f'mcp_servers.{key}.disabled_tools=["harness"]')
        if mode == "read-only":
            fields += [f"mcp_servers.{key}.command=", f"mcp_servers.{key}.args="]
    return fields


@pytest.mark.parametrize("mode", ["read-only", "write"])
@pytest.mark.parametrize(
    "entries",
    [
        {"neosian-memory": "stdio"},
        {"neosian-memory": "streamable_http"},
        {"neosian": "streamable_http"},
        {"neosian": "stdio", "neosian-cloud": "streamable_http"},
        {"neosian-cloud": "stdio"},
    ],
    ids=["stdio", "http-old-name", "http", "stdio-and-host-http", "host-stdio"],
)
def test_a_codex_child_starts_with_neosian_over_http_or_stdio(
    binary: Binary, harnesses: Harnesses, entries: dict[str, str], mode: str
) -> None:
    """Each key gets only its transport's fields (the stand-in refuses the
    rest as Codex does); in read-only every other server and every entry
    that is not this server's stdio entry is off, and nothing is switched on."""
    harnesses.enable()
    env = harnesses.env(STANDIN_CODEX_ENTRIES=json.dumps(entries))
    session = Session(binary, env)
    child = session.harness(command="start", agent="codex", prompt="x", mode=mode)
    done = finished(session, child["child"])
    assert done["state"] == "finished", done
    [run] = harnesses.runs()
    fields = overrides(run["argv"])
    ours = [f for f in fields if not f.startswith("mcp_servers.playwright.")]
    want = expected(entries, mode)
    assert len(ours) == len(want), (ours, want)
    assert all(got.startswith(prefix) for got, prefix in zip(ours, want, strict=True))
    assert run["switched_on"] == []
    if mode == "read-only":
        off = {"playwright"} | {
            key
            for key, kind in entries.items()
            if kind != "stdio" or key == "neosian-cloud"
        }
        assert set(run["switched_off"]) == off
    else:
        assert run["switched_off"] == []
    clean([child, done, run["argv"]])
    session.close()


def test_an_unknown_transport_is_refused_by_name(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Neither guessed nor repeated beyond its name: a key with a transport
    neosian does not know refuses both modes, at start and in the watcher;
    another server with one refuses read-only only."""
    harnesses.enable()
    env = harnesses.env(STANDIN_CODEX_ENTRIES='{"neosian": "websocket"}')
    session = Session(binary, env)
    for mode in ("read-only", "write"):
        refused = session.call(
            "harness", command="start", agent="codex", prompt="x", mode=mode
        )
        assert refused[0] and "neosian" in refused[1] and "websocket" in refused[1]
        assert "wss://" not in refused[1]
    session.close()
    watched = watch(binary, env, plan(harnesses.work, mode="write"))
    assert watched.returncode == 2 and b"websocket" in watched.stderr
    assert harnesses.runs() == []
    other = harnesses.env(STANDIN_CODEX_SERVERS='[["figma", "sse"]]')
    session = Session(binary, other)
    refused = session.call("harness", command="start", agent="codex", prompt="x")
    assert refused[0] and "figma" in refused[1] and "sse" in refused[1]
    child = session.harness(command="start", agent="codex", prompt="x", mode="write")
    assert finished(session, child["child"])["state"] == "finished"
    session.close()
