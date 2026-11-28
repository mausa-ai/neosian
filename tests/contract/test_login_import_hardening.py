"""The login import's hardening (the L1e slice of the L1 plan, rulings N2 and
N4 to N7), against the fake host of `login_import_host.py`.

A copy no journal names does not outlive its login. Recovery state outlives
a discard the host did not confirm. A status answer that names another
session, or reports success with counts that differ from the commit, is not
success. Local omissions keep their own reasons, also when nothing is left
to send. A fresh session may leave `received` out; this machine's open one
may not. The home's store data stays byte for byte as it was.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

from tests.contract.login_import_host import ImportHost
from tests.contract.records.importing import Conversation, Turn, message
from tests.contract.support import Binary
from tests.contract.test_login_import import (
    AREA,
    NAMES,
    QUESTION,
    hosting,
    interrupted,
    journal,
    last,
    login,
    routes,
    seed,
    sent,
    source,
    unix,
)
from tests.contract.test_login_output import ANSI
from tests.contract.test_machine_login import TRUST


def flat(text: str) -> str:
    return " ".join(text.split())


def killed_at_the_question(binary: Binary, host: ImportHost) -> None:
    """A first login in a terminal, killed while it asks whether to import."""
    import fcntl
    import pty
    import select
    import struct
    import termios

    master, slave = pty.openpty()
    # 24 rows by 80 columns, whatever size a fresh pty has on this platform.
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    process = subprocess.Popen(
        [str(binary.executable), "login", "--host", host.url + "/host"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=binary.directory,
        env={**binary.environment(), **TRUST},
    )
    os.close(slave)
    shown, deadline = "", time.monotonic() + 60
    try:
        while not re.search(QUESTION, ANSI.sub("", shown)):
            assert time.monotonic() < deadline, shown
            if select.select([master], [], [], 1)[0]:
                shown += os.read(master, 4096).decode()
    finally:
        process.kill()
        process.wait(timeout=10)
        os.close(master)


@unix
def test_a_copy_without_consent_does_not_outlive_its_login(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host:
        killed_at_the_question(binary, host)
        assert (binary.home / AREA / "snapshot").is_dir()
        assert not (binary.home / AREA / "journal.json").exists()
        assert host.intents == [] and host.imports() == []
        plain = login(binary, host, "--workspace", "none", "--verbose")
        assert plain.returncode == 0, plain.stderr
        assert "Removed a copy of your memory that a login left" in plain.stderr
        assert host.imports() == []
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


def test_an_unconfirmed_discard_keeps_the_import(binary: Binary) -> None:
    before = seed(binary)
    refuse = dict.fromkeys(NAMES, (413, "unit_too_large"))
    with hosting(refuse=refuse, lose={"discard": {1}}) as host:
        first = login(binary, host, "--workspace", "none", "--import", "--json")
        assert first.returncode == 1, first.stderr
        report = last(first)["import"]
        assert (report["status"], report["kept"]) == ("stopped", True)
        assert journal(binary)["stage"] == "transfer"
        assert (binary.home / AREA / "snapshot").is_dir()
        (session,) = host.sessions.values()
        assert session.status == "open"
        mark = len(host.log)
        again = login(binary, host, "--import", "--json")
        assert again.returncode == 0, again.stderr
        assert last(again)["import"]["status"] == "nothing"
        assert routes(host, mark) == ["POST /", "DELETE /ID"]
        assert session.status == "discarded" and sent(host, mark) == []
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


def resumed_after(binary: Binary, host: ImportHost, **fixed: Any) -> None:
    """The first run stopped on the status answer, with its state kept;
    once the host answers right, `login --import` reads the result."""
    assert journal(binary)["stage"] == "committed"
    assert (binary.home / AREA / "snapshot").is_dir()
    for key, value in fixed.items():
        setattr(host, key, value)
    mark = len(host.log)
    again = login(binary, host, "--import", "--json")
    assert again.returncode == 0, again.stderr
    assert last(again)["import"]["status"] == "succeeded"
    assert routes(host, mark) == ["GET /ID"] and sent(host, mark) == []


def test_a_status_for_another_session_is_not_success(binary: Binary) -> None:
    before = seed(binary)
    with hosting(status_id="0" * 32) as host:
        first = login(binary, host, "--workspace", "none", "--import", "--json")
        assert first.returncode == 1, first.stderr
        assert last(first)["import"]["status"] == "stopped"
        assert "is not this import's result (another_session)" in flat(first.stderr)
        resumed_after(binary, host, status_id=None)
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


def test_a_success_whose_counts_differ_is_not_success(binary: Binary) -> None:
    before = seed(binary)
    with hosting(counts=(5, 0)) as host:
        first = login(binary, host, "--workspace", "none", "--import", "--json")
        assert first.returncode == 1, first.stderr
        report = last(first)["import"]
        assert (report["status"], report["kept"]) == ("stopped", True)
        assert "is not this import's result (counts)" in flat(first.stderr)
        resumed_after(binary, host, counts=None)
    assert source(binary) == before


def test_local_omissions_keep_their_reasons(binary: Binary) -> None:
    entry = {"turn": 1, "kind": "digest", "text": "d", "span": 1}
    Conversation("cc-empty", [], [entry]).write(binary.home)
    with hosting() as host:
        alone = login(binary, host, "--workspace", "none", "--import")
        assert alone.returncode == 0, alone.stderr
        said = flat(alone.stderr)
        assert "Left out conversation cc-empty: it holds no turns" in said
        assert "This machine holds no memory to import." in said
        assert host.imports() == []
    mixed = Binary(binary.executable, binary.directory / "mixed")
    mixed.directory.mkdir()
    seed(mixed)
    huge = {"turn": 1, "kind": "digest", "text": "x" * 1_000_000, "span": 1}
    turn = Turn(1, "2026-11-01T10:00:00Z", [message("user", "hi")], None)
    Conversation("cc-huge", [turn], [huge]).write(mixed.home)
    with hosting() as host:
        result = login(mixed, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
    said = flat(result.stderr)
    assert "1 conversation without turns will stay on this machine." in said
    assert (
        "1 conversation with an item larger than one restore part can carry will "
        "stay on this machine." in said
    )
    reasons = {o["name"]: o["reason"] for o in last(result)["import"]["omitted"]}
    assert reasons == {"cc-empty": "no_turns", "cc-huge": "unfit"}


def test_received_may_be_absent_from_a_fresh_session_only(binary: Binary) -> None:
    before = seed(binary)
    with hosting(received_on={200}) as host:
        fresh = login(binary, host, "--workspace", "none", "--import", "--json")
        assert fresh.returncode == 0, fresh.stderr
        assert last(fresh)["import"]["status"] == "succeeded"
    other = Binary(binary.executable, binary.directory / "resumed")
    other.directory.mkdir()
    seed(other)
    with hosting() as host:
        interrupted(other, host)
        host.received_on, mark = {201}, len(host.log)
        stopped = login(other, host, "--import", "--json")
        assert stopped.returncode == 1, stopped.stderr
        assert last(stopped)["import"]["status"] == "stopped"
        assert routes(host, mark) == ["POST /"] and sent(host, mark) == []
        assert journal(other)["stage"] == "transfer"
        host.received_on = {200, 201}
        again = login(other, host, "--import", "--json")
        assert last(again)["import"]["status"] == "succeeded", again.stderr
    assert source(binary) == before
