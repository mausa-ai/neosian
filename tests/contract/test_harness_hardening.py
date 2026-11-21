"""The hardening after the acceptance walkthroughs of 7a9d537
(record/x2c-walkthroughs-2026-11-21): a Codex server listed without a usable
name, a Codex registration made through a wrapper, a store URL carrying user
information, and a running Codex command."""

from __future__ import annotations

import os

import pytest

from tests.contract.harness_support import Harnesses, Session
from tests.contract.support import Binary
from tests.contract.test_harness_rereview import finished, plan, watch

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the tool is absent on Windows")


@pytest.fixture
def harnesses(binary: Binary) -> Harnesses:
    return Harnesses(binary).install()


@pytest.mark.parametrize(
    "entry",
    [
        '{"enabled": true, "transport": {"type": "stdio", "env": {"T": "s3cret"}}}',
        '{"name": 7, "enabled": true}',
        '{"name": null}',
        '"playwright"',
    ],
)
def test_a_codex_server_listed_without_a_usable_name_refuses_read_only(
    binary: Binary, harnesses: Harnesses, entry: str
) -> None:
    """An entry no override can address is never left switched on: read-only
    refuses, as for any other name an override cannot address, naming the
    entry by its position, never by its content."""
    harnesses.enable()
    env = harnesses.env(STANDIN_CODEX_LIST_EXTRA=f"[{entry}]")
    session = Session(binary, env)
    refused = session.call("harness", command="start", agent="codex", prompt="x")
    assert refused[0] and "without a usable name (entry 3 " in refused[1], refused
    assert "s3cret" not in refused[1]
    assert harnesses.runs() == []
    child = session.harness(command="start", agent="codex", prompt="x", mode="write")
    assert finished(session, child["child"])["state"] == "finished"
    session.close()


def test_a_read_only_codex_child_runs_this_binary_not_a_registered_wrapper(
    binary: Binary, harnesses: Harnesses
) -> None:
    """Neosian's Codex entry gets the running binary as its command as well
    as the read-only arguments, so a wrapper it was registered through never
    runs with them."""
    harnesses.enable()
    ran = binary.directory / "wrapper-ran"
    wrapper = binary.directory / "neosian-wrapper"
    wrapper.write_text(
        f'#!/bin/sh\ntouch "{ran}"\nexec "{binary.executable}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    session = Session(binary, harnesses.env(STANDIN_CODEX_COMMAND=str(wrapper)))
    child = session.harness(command="start", agent="codex", prompt="probe-ro")
    finished(session, child["child"])
    run = harnesses.runs()[0]
    server = run["read_only_server"]
    assert server["command"] == str(binary.executable)
    assert not ran.exists()
    assert server["create_error"] and "is read-only" in server["create_text"]
    expected = f'mcp_servers.neosian-memory.command="{binary.executable}"'
    assert expected in run["argv"]
    session.close()


def test_the_watcher_refuses_a_store_url_carrying_user_information(
    binary: Binary, harnesses: Harnesses
) -> None:
    """A credential in the URL would reach a read-only child's command line."""
    harnesses.enable()
    env = harnesses.env()
    for url in (
        "https://user:secret@example.com",
        "https://user@example.com/state",
        "http://a:b@127.0.0.1:9/",
    ):
        refused = watch(binary, env, plan(harnesses.work, store=["--url", url]))
        assert refused.returncode == 2, (url, refused.stderr)
        assert b"store" in refused.stderr, refused.stderr
    assert harnesses.runs() == []


def test_a_running_codex_command_is_reported_once_started(
    binary: Binary, harnesses: Harnesses
) -> None:
    """A command the child is running is an event before it completes, and
    its completion reads as the same command, not a second one."""
    harnesses.enable()
    session = Session(binary, harnesses.env())
    session.harness(command="start", agent="codex", prompt="running:4")
    first = session.harness(command="wait", child="h1", seconds=2)
    command = "/bin/zsh -lc 'sleep 4'"
    assert first["state"] == "running"
    assert first["events"] == [
        {"kind": "command_started", "id": "item_1", "command": command}
    ]
    done = finished(session, "h1")
    assert done["state"] == "finished"
    assert done["events"] == [
        {"kind": "command", "id": "item_1", "command": command, "exit": 0}
    ]
    session.close()
