"""How `neosian login` reads in text mode: named steps on stderr, one recording
notice, one line for each agent and the actions left. Plain on a pipe, wrapped
to a terminal's width, and the JSON lines and exit codes as they were."""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tests.contract.profile_support import run
from tests.contract.support import Binary
from tests.contract.test_machine_login import NOTICE, SECRET, TRUST, Host, host_server

BEARER = {**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
ACCOUNT = {"id": "account-one", "email": "user@example.test"}
unix = pytest.mark.skipif(os.name == "nt", reason="a pseudo-terminal needs Unix")


def line(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":")) + "\n"


# Text-mode stdout as the released login (f82d7ed1) wrote it; a script may read it.
RELEASED = re.compile(
    r"^(Connected as .*\. Managed profiles: .*\.|Removed assignments: .*"
    r"|Run neosian setup --write to register your agents, then restart them\.)$"
)


def released(managed: str, removed: str = "") -> str:
    return (
        f"Connected as user@example.test. Managed profiles: {managed}.\n"
        + (f"Removed assignments: {removed}\n" if removed else "")
        + "Run neosian setup --write to register your agents, then restart them.\n"
    )


def headings(text: str) -> list[str]:
    return [
        row
        for row in text.splitlines()
        if row and not row.startswith(" ") and not RELEASED.match(row)
    ]


@contextmanager
def login_host() -> Iterator[tuple[Host, str]]:
    with host_server() as host:
        yield host, host.url + "/host"


def test_a_piped_login_is_plain_and_says_the_notice_once(binary: Binary) -> None:
    # Colour is allowed by the environment; a pipe still gets none.
    env = {**BEARER, "NO_COLOR": "", "TERM": "xterm-256color"}
    with login_host() as (host, url):
        first = run(binary, "login", "--host", url, "--workspace", "none", env=env)
        assert first.returncode == 0, first.stderr
        assert first.stderr == (
            "Sign in\n"
            "  Using the machine credential in NEOSIAN_LOGIN_TOKEN.\n"
            "  ok  Connected to Test host as user@example.test\n"
            "\n"
            "Recording\n"
            "  Notice from Test host:\n"
            f"  | {NOTICE}\n"
            "  Recording is on for workspaces that allow it. To turn it off on this machine,\n"
            "  run neosian login --refresh --capture off.\n"
            "\n"
            "Workspaces\n"
            "  ok  Ready: personal (default)\n"
            "\n"
            "Next\n"
            "  1. Connect your agents with neosian setup --write, then restart them.\n"
        )
        again = run(binary, "login", "--refresh", env=env)
        assert again.returncode == 0
        assert again.stderr == (
            "Refresh\n"
            "  ok  Assignments unchanged for user@example.test\n"
            "\n"
            "Workspaces\n"
            "  ok  Ready: personal\n"
        )
        host.names, host.etag = ("personal", "assigned"), '"2"'
        changed = run(binary, "login", "--refresh", env=env)
        assert headings(changed.stderr) == [
            "Refresh",
            "Recording",
            "Workspaces",
            "Next",
        ]
        assert changed.stderr.count(NOTICE) == 1
        assert "  ok  Ready: assigned, personal (default)\n" in changed.stderr
        assert "Restart your agents so they use the refreshed workspaces." in (
            changed.stderr
        )
        host.account, host.etag = "account-two", '"other"'
        refused = run(binary, "login", "--refresh", env=env)
        assert refused.returncode == 1 and refused.stdout == ""
        assert refused.stderr == (
            "Refresh\n"
            "  x   a different account manages this home; use another NEOSIAN_HOME or logout\n"
            "      --forget\n"
        )


def test_text_stdout_keeps_the_released_lines(binary: Binary) -> None:
    """Literals from the baseline binary's text-mode stdout."""
    with login_host() as (host, url):
        first = run(binary, "login", "--host", url, "--workspace", "none", env=BEARER)
        assert (first.returncode, first.stdout) == (0, released('["personal"]'))
        unchanged = run(binary, "login", "--refresh", env=BEARER)
        assert (unchanged.returncode, unchanged.stdout) == (0, released('["personal"]'))
        host.names, host.etag = ("personal", "assigned"), '"2"'
        added = run(binary, "login", "--refresh", env=BEARER)
        assert added.stdout == released('["assigned","personal"]')
        host.names, host.etag = ("personal",), '"3"'
        removed = run(binary, "login", "--refresh", env=BEARER)
        assert removed.stdout == released('["personal"]', '["assigned"]')
        host.account, host.etag = "account-two", '"4"'
        refused = run(binary, "login", "--refresh", env=BEARER)
        assert (refused.returncode, refused.stdout) == (1, "")


def test_json_lines_and_exit_codes_are_unchanged(binary: Binary) -> None:
    connected = {
        "event": "connected",
        "updated": True,
        "account": ACCOUNT,
        "device": None,
        "default_profile": "personal",
        "managed": ["personal"],
        "removed": [],
        "project": None,
        "capture": "per_profile",
    }
    with login_host() as (host, url):
        args = ("login", "--host", url, "--workspace", "none", "--json")
        headless = run(binary, *args, "--non-interactive", env=BEARER)
        assert (headless.returncode, headless.stdout) == (0, line(connected))
        unchanged = run(binary, "login", "--refresh", "--json", env=BEARER)
        assert (unchanged.returncode, unchanged.stdout) == (
            0,
            line(
                {
                    "event": "refreshed",
                    "updated": False,
                    "account": ACCOUNT,
                    "device": None,
                    "managed": ["personal"],
                }
            ),
        )
    with login_host() as (host, url):
        args = ("login", "--host", url, "--workspace", "none", "--json")
        other = Binary(binary.executable, binary.directory / "device")
        other.directory.mkdir()
        device = run(other, *args, env=TRUST)
        start = {
            "event": "device_authorization",
            "verification_uri": host.url + "/approve",
            "verification_uri_complete": host.url + "/approve?user_code=BCDF-GHJK",
            "user_code": "BCDF-GHJK",
            "expires_in": 30,
        }
        issued = {**connected, "device": {"id": "device-id", "name": "fixture"}}
        assert (device.returncode, device.stdout) == (0, line(start) + line(issued))
        assert device.stderr.count(NOTICE) == 1 and ANSI.search(device.stderr) is None
        host.deny, host.polls = True, 0
        denied_home = Binary(binary.executable, binary.directory / "denied")
        denied_home.directory.mkdir()
        denied = run(denied_home, *args, env=TRUST)
        assert (denied.returncode, denied.stdout.splitlines()[-1]) == (
            1,
            line({"error": "login", "hint": "device authorization denied"}).strip(),
        )
        text = run(denied_home, "login", "--host", url, env=TRUST)
        assert text.returncode == 1 and text.stdout == ""
        assert text.stderr.endswith("  x   device authorization denied\n")


def test_host_text_is_cleaned_before_it_is_shown(binary: Binary) -> None:
    with login_host() as (host, url):
        host.notice = "Saved \u202eby the host.\u200b Read it."
        shown = run(binary, "login", "--host", url, "--workspace", "none", env=BEARER)
        assert shown.returncode == 0
        assert "  | Saved by the host. Read it.\n" in shown.stderr
        assert "\u202e" not in shown.stderr and "\u200b" not in shown.stderr
        host.notice, host.etag = "Saved \x1b[2J\x1b]0;neosian\x07 by the host.", '"3"'
        refused = run(binary, "login", "--refresh", env=BEARER)
        assert refused.returncode == 0 and "\x1b" not in refused.stderr
        assert "Recording is off on this machine." in refused.stderr


def terminal(
    binary: Binary,
    argv: list[str],
    env: dict[str, str],
    answers: list[tuple[str, str]],
    columns: int = 80,
) -> tuple[int, str]:
    """Run in a pseudo-terminal `columns` wide, answering each prompt in turn."""
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, columns, 0, 0))
    process = subprocess.Popen(
        [str(binary.executable), *argv],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=binary.directory,
        env={**binary.environment(), **env, "SSH_CONNECTION": "contract"},
    )
    os.close(slave)
    output, pending, deadline = b"", list(answers), time.monotonic() + 30
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        if pending and re.search(pending[0][0], ANSI.sub("", output.decode())):
            os.write(master, pending.pop(0)[1].encode())
        if not ready and process.poll() is not None:
            break
    process.wait(timeout=10)
    os.close(master)
    return process.returncode, output.decode().replace("\r\n", "\n")


def clients(binary: Binary, codex_cli: bool) -> dict[str, str]:
    for name in (".cursor", ".codex"):
        (binary.directory / name).mkdir(exist_ok=True)
    tools = binary.directory / "tools"
    tools.mkdir(exist_ok=True)
    if codex_cli:
        codex = tools / "codex"
        codex.write_text("#!/bin/sh\nexit 0\n")
        codex.chmod(0o755)
    return {"PATH": f"{tools}{os.pathsep}/usr/bin{os.pathsep}/bin"}


AGENTS = [(r"\[y/N\]", "y\n"), (r"q cancels:", "\n")]


@unix
def test_a_first_login_in_a_terminal_reads_as_steps(binary: Binary) -> None:
    checkout = binary.directory / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    env = {**TRUST, **clients(binary, True), "TERM": "xterm-256color", "NO_COLOR": ""}
    with login_host() as (_, url):
        code, raw = terminal(
            binary,
            ["login", "--host", url, "--project", str(checkout)],
            env,
            [(r"\[none\]:", "personal\n"), *AGENTS],
        )
    assert code == 0, raw
    assert "\x1b[1mApprove this machine\x1b[0m" in raw and "\x1b[32mok\x1b[0m" in raw
    text = ANSI.sub("", raw)
    assert headings(text) == [
        "Approve this machine",
        "This directory",
        "Recording",
        "Workspaces",
        "Agents",
        "Next",
    ]
    assert text.count(NOTICE) == 1 and "Workspaces with shared recording" not in text
    assert "  Code  BCDF-GHJK\n" in text
    assert "  none  changes nothing here; an unbound directory uses the default" in text
    # Setup's own checklist chose the agents; each has one line.
    assert "  Connect clients\n    1. [x] Codex\n    2. [x] Cursor\n" in text
    assert "  ok  Codex\n  ok  Cursor\n" in text
    assert "  1. Restart your agents.\n" in text
    assert "  2. In Codex, open /hooks and approve the new hook.\n" in text
    # Paths, commands and setup's JSON stay behind --verbose.
    assert "mcp.json" not in text and "sources [" not in text
    assert "not found; searched" not in text


@unix
def test_a_narrow_terminal_without_colour_declines_setup(binary: Binary) -> None:
    with login_host() as (_, url):
        code, text = terminal(
            binary,
            ["login", "--host", url, "--workspace", "none"],
            {**BEARER, **clients(binary, False)},
            [(r"\[y/N\]", "n\n")],
            columns=40,
        )
    assert code == 0 and "\x1b" not in text, text
    for row in text.splitlines():
        assert len(row) <= 40 or " " not in row.strip() or RELEASED.match(row), row
    assert text.count("Notice from Test host:") == 1
    assert text.endswith(
        "Next\n  1. Connect your agents with neosian\n     setup --write, then restart them.\n"
    )


@unix
def test_a_partial_setup_failure_names_the_agent(binary: Binary) -> None:
    env = {**BEARER, **clients(binary, False)}
    with login_host() as (host, url):
        code, text = terminal(
            binary, ["login", "--host", url, "--workspace", "none"], env, AGENTS
        )
        assert code == 1, text
        assert (
            "  x   Codex: MCP registration not applied (codex is not on PATH)\n"
            "  ok  Cursor\n"
        ) in text
        assert text.endswith(
            "Next\n"
            "  1. Restart your agents.\n"
            "  2. In Codex, open /hooks and approve the new hook.\n"
            "  3. See what Codex needs with neosian setup --client codex.\n"
        )
        host.etag = '"verbose"'
        code, verbose = terminal(
            binary,
            ["login", "--host", url, "--workspace", "none", "--verbose"],
            env,
            AGENTS,
        )
    assert code == 1
    assert ".cursor/mcp.json  " in verbose and "not found; searched" in verbose
    assert "  Device   -\n" in verbose and "  Account  account-one\n" in verbose
    assert verbose.count(NOTICE) == 1


def test_standalone_setup_keeps_its_own_report(binary: Binary) -> None:
    (binary.directory / ".cursor").mkdir()
    result = run(binary, "setup", "--write", "--client", "cursor", env={"PATH": ""})
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("running   ")
    assert (
        "Cursor\n  mcp   " in result.stdout and "Registrations saved." in result.stdout
    )
