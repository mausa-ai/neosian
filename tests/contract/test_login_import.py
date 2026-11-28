"""`neosian login` brings local memory to the host it signs in to (the L1c
slice of the L1 plan), against the fake host of `login_import_host.py`.

Asked once, default no; `--import` answers yes. A decline, a host without
`import_endpoint`, an empty home, a non-interactive login and a refresh send
no import request at all. Begin, every unit, the commit and status follow
the host contract; a unit the host refuses by name is left out and named; an
interruption during the transfer resumes with the same session and the
units the host holds are not sent again; a lost commit answer is settled by
status, never by sending units again. A waiting import is resumed only with
the host, account and credential it began with. The home's store data is
byte for byte the same after every run.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tests.contract.login_import_host import ImportHost, serving
from tests.contract.profile_support import run
from tests.contract.records.importing import Conversation, Turn, message
from tests.contract.records.snapshotting import tree
from tests.contract.support import Binary
from tests.contract.test_login_output import ANSI, terminal
from tests.contract.test_machine_login import SECRET, TRUST

BEARER = {**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}
AREA = "login-import"
OWNED = {AREA, "login", "profiles", "profile-bindings.toml", ".profile.lock"}
QUESTION = r"Bring this memory to Test host\? \[y/N\]"
AGENTS = (r"Connect your agents now\? \[y/N\]", "n\n")
NAMES = ["user:contract", "user:contract/proj:demo", "cc-a", "cc-b", "cc-c"]
NO_TURNS = {"kind": "conversation", "name": "cc-empty", "reason": "no_turns"}
unix = pytest.mark.skipif(os.name == "nt", reason="a pseudo-terminal needs Unix")
pytestmark = pytest.mark.timeout(300)


def talk(name: str, turns: int) -> Conversation:
    rows = [
        Turn(n, f"2026-11-0{n}T10:00:00Z", [message("user", f"{name} {n}")], "cli:me")
        for n in range(1, turns + 1)
    ]
    return Conversation(name, rows)


def seed(binary: Binary) -> dict[str, str]:
    """Two memory scopes, three conversations with turns and one without;
    the home's store data by path and digest."""
    for scope, text in (("user:contract", "alpha"), ("user:contract/proj:demo", "b")):
        made = binary.run(
            "memory", "create", "/memories/a", "--scope", scope, "--content", text
        )
        assert made.returncode == 0, made.stderr
    for conversation in (talk("cc-a", 2), talk("cc-b", 3), talk("cc-c", 1)):
        conversation.write(binary.home)
    entry = {"turn": 1, "kind": "digest", "text": "d", "span": 1}
    Conversation("cc-empty", [], [entry]).write(binary.home)
    return source(binary)


def source(binary: Binary) -> dict[str, str]:
    """The home's store data: everything login does not own."""
    found = tree(binary.home, area=True).items()
    return {k: v for k, v in found if k.split("/")[0] not in OWNED}


@contextmanager
def hosting(**settings: Any) -> Iterator[ImportHost]:
    with serving(ImportHost(**settings)) as host:
        yield host


def login(
    binary: Binary, host: ImportHost, *more: str, env: dict[str, str] = TRUST
) -> Any:
    return run(binary, "login", "--host", host.url + "/host", *more, env=env)


def routes(host: ImportHost, since: int = 0) -> list[str]:
    """Each import request: its method and the path below the session."""
    shown = []
    for e in host.log[since:]:
        if e.path.startswith("/import-sessions"):
            path = re.sub(
                r"/[0-9a-f]{32}", "/ID", e.path.removeprefix("/import-sessions")
            )
            kind = "probe" if path.endswith("records") and len(e.body) == 2 else ""
            shown.append(f"{e.method} {path or '/'} {kind}".strip())
    return shown


def sent(host: ImportHost, since: int = 0) -> list[str]:
    """The units whose data reached the host, in order."""
    names = []
    for e in host.log[since:]:
        if e.path.endswith("restore_scope"):
            names.append(e.body["scope"])
        elif (
            e.path.endswith("restore_records")
            and len(e.body) > 2
            and not e.body.get("discard")
        ):
            names.append(e.body["conversation_id"])
    return names


def last(result: Any) -> Any:
    return json.loads(result.stdout.splitlines()[-1])


def journal(binary: Binary) -> Any:
    path = binary.home / AREA / "journal.json"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    text = path.read_text()
    assert SECRET not in text
    return json.loads(text)


FLOW = [
    "POST /",
    "POST /ID/wire/v1/store/restore_scope",
    "POST /ID/wire/v1/store/restore_scope",
    *[
        "POST /ID/wire/v1/store/restore_records probe",
        "POST /ID/wire/v1/store/restore_records",
    ]
    * 3,
    "POST /ID/commit",
    "GET /ID",
    "GET /ID",
]


def test_import_flag_sends_every_unit_and_commits(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host:
        result = login(binary, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
        assert host.intents == [
            {"scopes": 2, "conversations": 3, "bytes": host.intents[0]["bytes"]}
        ]
        assert routes(host) == FLOW
        assert all(e.auth == f"Bearer {SECRET}" for e in host.imports())
        commit = next(e.body for e in host.log if e.path.endswith("/commit"))
    assert [u["name"] for u in commit["units"]] == NAMES
    assert commit["omitted"] == [] and commit["personal"] == "user:contract"
    report = last(result)["import"]
    assert (report["status"], report["kept"]) == ("succeeded", False)
    assert [u["name"] for u in report["units"]] == NAMES
    assert report["units"] == commit["units"]
    assert report["omitted"] == [NO_TURNS]
    assert {"source": "user:contract", "mount": "me", "profile": None} in report[
        "targets"
    ]
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


@unix
def test_a_yes_in_a_terminal_imports_and_says_so(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host:
        code, raw = terminal(
            binary,
            ["login", "--host", host.url + "/host", "--workspace", "none"],
            TRUST,
            [(QUESTION, "y\n"), AGENTS],
        )
        assert code == 0, raw
        assert routes(host) == FLOW
    text = ANSI.sub("", raw)
    assert text.count("Bring this memory") == 1
    assert "This machine holds 2 memory scopes and 3 conversations," in text
    assert "1 conversation without turns will stay on this machine." in text
    assert "it may contain secrets. Nothing is removed." in text
    assert "approve the machine without the import." in text
    assert "  ok  Imported 2 memory scopes and 3 conversations" in text
    assert "  !   Left out conversation cc-empty: it holds no turns" in text
    # This directory's name gave no imported scope (L1d).
    assert "so it keeps the default workspace, personal." in " ".join(text.split())
    order = [text.index(s) for s in ("Your memory", "Approve this", "Import\n", "Next")]
    assert order == sorted(order)
    assert source(binary) == before


@unix
def test_a_no_sends_nothing_and_keeps_no_copy(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host:
        code, raw = terminal(
            binary,
            ["login", "--host", host.url + "/host", "--workspace", "none"],
            TRUST,
            [(QUESTION, "n\n"), AGENTS],
        )
        assert code == 0, raw
        assert host.intents == [None] and host.imports() == []
    assert "Not importing. Your memory stays on this machine." in ANSI.sub("", raw)
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


@unix
def test_a_host_without_import_is_never_mentioned(binary: Binary) -> None:
    before = seed(binary)
    with hosting(offers_import=False) as host:
        code, raw = terminal(
            binary,
            ["login", "--host", host.url + "/host", "--workspace", "none"],
            TRUST,
            [AGENTS],
        )
        assert code == 0, raw
        shown = ANSI.sub("", raw)
        assert "Your memory" not in shown and "import" not in shown.lower()
        assert host.intents == [None] and host.imports() == []
        asked = login(binary, host, "--import")
        assert asked.returncode == 1
        assert "Test host does not offer an import" in asked.stderr
        assert host.intents == [None] and host.imports() == []
    assert source(binary) == before


def test_the_host_can_approve_the_machine_without_the_import(binary: Binary) -> None:
    before = seed(binary)
    with hosting(approve_import=False) as host:
        result = login(binary, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
        assert routes(host) == ["POST /"]
    assert last(result)["import"]["status"] == "not_authorized"
    assert last(result)["event"] == "connected"
    assert "approved this machine without the import" in result.stderr
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


def test_a_refused_unit_is_named_and_the_others_go_on(binary: Binary) -> None:
    before = seed(binary)
    with hosting(refuse={"cc-b": (413, "unit_too_large")}) as host:
        result = login(binary, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
        commit = next(e.body for e in host.log if e.path.endswith("/commit"))
        discards = [e.body for e in host.log if e.raw and b'"discard"' in e.raw]
    left = {"kind": "conversation", "name": "cc-b", "reason": "unit_too_large"}
    assert [u["name"] for u in commit["units"]] == [
        "user:contract",
        NAMES[1],
        "cc-a",
        "cc-c",
    ]
    assert commit["omitted"] == [left]
    assert [d["conversation_id"] for d in discards] == ["cc-b"]
    report = last(result)["import"]
    assert report["omitted"] == [NO_TURNS, left] and report["status"] == "succeeded"
    assert "  !   Left out conversation cc-b: the host refused it as too large." in (
        result.stderr
    )
    assert source(binary) == before


def interrupted(binary: Binary, host: ImportHost, at: int = 4) -> None:
    """A first login whose data request `at` is applied and its answer lost."""
    host.lose = {"data": {at}}
    first = login(binary, host, "--workspace", "none", "--import")
    assert first.returncode == 1, first.stderr
    assert first.stdout.startswith("Connected as user@example.test.")
    assert "  x   The import stopped: an answer from the host was lost" in first.stderr
    assert "1. Resume the import with neosian login --import, or discard it" in (
        first.stderr
    )
    assert journal(binary)["stage"] == "transfer"


def test_an_interrupted_transfer_resumes_the_same_session(binary: Binary) -> None:
    seed(binary)
    # Two parts: 24 turns of one 50,000-byte message each.
    rows = [
        Turn(n, f"2026-11-01T10:{n:02}:00Z", [message("user", "x" * 50_000)], None)
        for n in range(1, 25)
    ]
    Conversation("cc-big", rows).write(binary.home)
    before = source(binary)
    with hosting() as host:
        interrupted(binary, host, at=5)
        assert sent(host) == [*NAMES[:4], "cc-big"]
        mark = len(host.log)
        again = login(binary, host, "--import", "--json")
        assert again.returncode == 0, again.stderr
        assert last(again)["event"] == "import"
        assert last(again)["import"]["status"] == "succeeded"
        # The account first, then this machine's open session (200) with the
        # four units it holds; cc-big's probe finds its first part staged,
        # which is repeated before the second part commits; then cc-c.
        assert host.log[mark].path == "/machine"
        records = "POST /ID/wire/v1/store/restore_records"
        assert routes(host, mark) == [
            "POST /",
            f"{records} probe",
            records,
            records,
            f"{records} probe",
            records,
            "POST /ID/commit",
            "GET /ID",
            "GET /ID",
        ]
        assert sent(host, mark) == ["cc-big", "cc-big", "cc-c"]
        assert len(host.sessions) == 1 and len(host.intents) == 1
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


def test_a_lost_commit_answer_is_settled_by_status(binary: Binary) -> None:
    before = seed(binary)
    with hosting(lose={"commit": {1}}) as host:
        result = login(binary, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
        flow = routes(host)
        assert flow[flow.index("POST /ID/commit") + 1 :] == ["GET /ID", "GET /ID"]
        assert sent(host) == NAMES
    assert last(result)["import"]["status"] == "succeeded"
    assert source(binary) == before


def test_an_uncertain_commit_is_resumed_with_status_alone(binary: Binary) -> None:
    before = seed(binary)
    with hosting(lose={"commit": {1}, "status": {1}}) as host:
        first = login(binary, host, "--workspace", "none", "--import", "--json")
        assert first.returncode == 1, first.stderr
        assert journal(binary)["stage"] == "commit"
        assert "may or may not have been applied" in first.stderr
        mark = len(host.log)
        again = login(binary, host, "--import", "--json")
        assert again.returncode == 0, again.stderr
        assert routes(host, mark) == ["GET /ID", "GET /ID"]
        assert sent(host) == NAMES
    assert last(again)["import"]["status"] == "succeeded"
    assert source(binary) == before


def test_a_connected_machine_imports_with_the_flag(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host:
        plain = login(binary, host, "--workspace", "none", "--json")
        assert plain.returncode == 0 and "import" not in last(plain)
        assert host.imports() == [] and host.intents == [None]
        again = login(binary, host, "--import", "--json")
        assert again.returncode == 0, again.stderr
        assert host.intents[1]["conversations"] == 3
        assert routes(host) == FLOW
    assert last(again)["import"]["status"] == "succeeded"
    assert source(binary) == before


def test_a_waiting_import_goes_only_to_its_host(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host, hosting() as other:
        interrupted(binary, host)
        mark = len(host.log)
        plain = login(binary, other, "--workspace", "none")
        assert plain.returncode == 1 and "is waiting to finish" in plain.stderr
        moved = login(binary, other, "--import")
        assert moved.returncode == 1 and "waiting import belongs to" in moved.stderr
        assert other.log == [] and len(host.log) == mark
        assert journal(binary)["stage"] == "transfer"
    assert source(binary) == before


def test_a_waiting_import_needs_its_account_and_credential(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host:
        interrupted(binary, host)
        mark = len(host.log)
        host.account = "account-two"
        other = login(binary, host, "--import")
        assert other.returncode == 1 and "another account" in other.stderr
        assert [e.path for e in host.log[mark:]] == ["/machine"]
        host.account = "account-one"
        assert run(binary, "logout", env=TRUST).returncode == 0
        mark = len(host.log)
        signed_out = login(binary, host, "--import")
        assert (
            signed_out.returncode == 1
            and "credential is not the one" in signed_out.stderr
        )
        assert host.log[mark:] == []
        dropped = login(binary, host, "--discard-import", "--json")
        assert dropped.returncode == 0, dropped.stderr
        assert last(dropped) == {
            "event": "import_discarded",
            "removed": True,
            "host": None,
        }
        assert host.log[mark:] == []
    assert not (binary.home / AREA).exists()
    assert source(binary) == before


@unix
def test_unattended_logins_and_refresh_never_import(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as host:
        args = ["login", "--host", host.url + "/host", "--workspace", "none"]
        code, raw = terminal(
            binary, [*args, "--non-interactive"], BEARER, [(QUESTION, "y\n")]
        )
        assert code == 0, raw
        code, raw = terminal(
            binary, ["login", "--refresh"], BEARER, [(QUESTION, "y\n")]
        )
        assert code == 0, raw
        assert "Bring this memory" not in raw and "Your memory" not in raw
        flag = login(binary, host, "--import", env=BEARER)
        assert flag.returncode == 1 and "unset NEOSIAN_LOGIN_TOKEN" in flag.stderr
        clash = login(binary, host, "--import", "--non-interactive", env=BEARER)
        assert clash.returncode == 2
        assert host.imports() == [] and host.intents == []
    with hosting() as host:
        device = Binary(binary.executable, binary.directory / "device")
        device.directory.mkdir()
        seed(device)
        assert login(device, host, "--workspace", "none").returncode == 0
        code, raw = terminal(device, ["login", "--refresh"], TRUST, [(QUESTION, "y\n")])
        assert code == 0 and "Bring this memory" not in raw, raw
        assert host.imports() == [] and host.intents == [None]
    assert source(binary) == before


@unix
def test_an_empty_home_is_not_offered(binary: Binary) -> None:
    with hosting() as host:
        args = ["login", "--host", host.url + "/host", "--workspace", "none"]
        code, raw = terminal(binary, args, TRUST, [AGENTS])
        assert code == 0 and "Bring this memory" not in raw, raw
        flag = login(binary, host, "--import")
        assert flag.returncode == 0, flag.stderr
        assert "This machine holds no memory to import." in flag.stderr
        assert host.imports() == [] and host.intents == [None, None]


def test_a_transfer_url_on_another_origin_gets_nothing(binary: Binary) -> None:
    before = seed(binary)
    with hosting() as other, hosting() as host:
        host.elsewhere = other.url + "/import-sessions/x"
        result = login(binary, host, "--workspace", "none", "--import")
        assert result.returncode == 1
        assert "different origin" in result.stderr
        assert other.log == [] and routes(host) == ["POST /"]
    assert source(binary) == before


def test_work_the_host_continues_is_kept_and_read_later(binary: Binary) -> None:
    before = seed(binary)
    with hosting(result="waiting") as host:
        first = login(binary, host, "--workspace", "none", "--import", "--json")
        assert first.returncode == 0, first.stderr
        report = last(first)["import"]
        assert (report["status"], report["kept"]) == ("waiting", True)
        assert report["waiting_reason"] == "daily_quota"
        assert "The host continues without this machine (daily_quota" in first.stderr
        assert journal(binary)["stage"] == "committed"
        host.result, mark = "succeeded", len(host.log)
        again = login(binary, host, "--import", "--json")
        assert last(again)["import"]["status"] == "succeeded"
        assert routes(host, mark) == ["GET /ID"]
    assert not (binary.home / AREA).exists()
    assert source(binary) == before
