"""What waits and what is said about it (rulings E2, E3, E4 and E6 of the T1e1
plan): logout reports closed turns that have not landed, a turn closed under
another token waits while later turns land, an unreadable span fails its Stop
as before and is set aside with its bytes as they were, and a paused span is
discarded, never held, by a policy change."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.contract.landing.conftest import Serve
from tests.contract.landing.test_queue import SESSION, credential
from tests.contract.profile_support import install, run
from tests.contract.support import ADMIN, Binary
from tests.contract.test_machine_login import (
    SECRET,
    TRUST,
    headless,
    host_server,
    record as managed,
)
from tests.contract.test_profile_capture import capture_profile, contents, event
from tests.contract.test_profile_pause import local

HINT = "the record never blocks the agent"


def hook(
    binary: Binary, store: list[str], token: str, name: str, **payload: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(binary.executable),
            "record",
            *store,
            "--scope",
            "user:contract",
            "--json",
        ],
        input=json.dumps({"session_id": SESSION, "hook_event_name": name, **payload}),
        env={**binary.environment(), "NEOSIAN_CLIENT_TOKEN": token},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def turn(
    binary: Binary, store: list[str], token: str, prompt: str
) -> subprocess.CompletedProcess[str]:
    opened = hook(binary, store, token, "UserPromptSubmit", prompt=prompt)
    assert opened.returncode == 0, opened.stderr
    return hook(binary, store, token, "Stop", last_assistant_message=prompt + " done")


def test_logout_reports_closed_turns_that_have_not_landed(binary: Binary) -> None:
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        opened = managed(binary, SECRET, "UserPromptSubmit", prompt="waits")
        assert opened.returncode == 0
        # The test host answers no conversation route, so the landing fails.
        assert (
            managed(binary, SECRET, "Stop", last_assistant_message="r").returncode == 1
        )
        spool = binary.home / "profile-spool"
        assert not list(spool.glob("*.policy"))
        assert len(list(spool.glob("pending/*/1.jsonl"))) == 1
        logged_out = run(binary, "logout", "--json", env=TRUST)
        assert logged_out.returncode == 0, logged_out.stderr
        assert json.loads(logged_out.stdout)["pending_capture"] is True
        assert (
            "Closed turns that have not landed remain in the private spool."
            in logged_out.stderr
        )
        assert "An open turn remains" not in logged_out.stderr


def test_logout_reports_a_closed_turn_that_is_only_held(binary: Binary) -> None:
    """Ruling E17: a held turn has not landed either."""
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        opened = managed(binary, SECRET, "UserPromptSubmit", prompt="held")
        conversation = json.loads(opened.stdout)["session_id"]
        assert (
            managed(binary, SECRET, "Stop", last_assistant_message="r").returncode == 1
        )
        # Set the closed turn aside as a hold does: its reason, then the turn.
        spool = binary.home / "profile-spool"
        pending, held = (spool / queue / conversation for queue in ("pending", "held"))
        held.mkdir(parents=True)
        (held / "1.reason").write_text('{"reason":"refused"}\n')
        for part in ("1.state", "1.jsonl"):
            (pending / part).rename(held / part)
        assert not list(spool.glob("pending/*/*.jsonl"))
        logged_out = run(binary, "logout", "--json", env=TRUST)
        assert logged_out.returncode == 0, logged_out.stderr
        assert json.loads(logged_out.stdout)["pending_capture"] is True
        assert (
            "Closed turns that have not landed remain in the private spool."
            in logged_out.stderr
        )


def test_a_turn_closed_under_another_token_waits_and_later_turns_land(
    binary: Binary, port: int, serve_on: Serve
) -> None:
    url = f"http://127.0.0.1:{port}"
    slashed = ["--url", url + "/"]
    assert turn(binary, slashed, "earlier-token", "one").returncode == 1
    assert turn(binary, slashed, ADMIN, "two").returncode == 1
    pending = binary.home / "spool" / "pending" / SESSION
    states = [json.loads((pending / f"{n}.state").read_text()) for n in (1, 2)]
    assert [s["destination"]["url"] for s in states] == [url, url]
    assert [s["destination"]["credential_sha256"] for s in states] == [
        credential(url, "earlier-token"),
        credential(url, ADMIN),
    ]
    with serve_on(port) as server:
        # The same destination without the trailing slash lands turn 2 and its own.
        landed = turn(binary, ["--url", url], ADMIN, "three")
        assert landed.returncode == 0, landed.stderr
        assert json.loads(landed.stdout)["turn"] == 2
        turns = server.post("conversation/read_turns", conversation_id=SESSION)
        assert [t["messages"][0]["content"] for t in turns["turns"]] == ["two", "three"]
    assert sorted(path.name for path in pending.iterdir()) == [
        ".lock",
        "1.jsonl",
        "1.state",
    ]


@pytest.mark.parametrize(
    ("planted", "refused"),
    [
        (b"7\n", "line 1 is not an object"),
        (b'{"kind": "prompt", "text": "\xff"}\n', "stream did not contain valid UTF-8"),
    ],
)
def test_an_unreadable_span_fails_its_stop_and_later_turns_land(
    binary: Binary, planted: bytes, refused: str
) -> None:
    store = ["--root", str(binary.directory / "store")]
    spool = binary.home / "spool"
    spool.mkdir(parents=True)
    (spool / f"{SESSION}.jsonl").write_bytes(planted)
    stopped = hook(binary, store, ADMIN, "Stop", last_assistant_message="lost")
    assert stopped.returncode == 1
    answer = json.loads(stopped.stdout)
    assert answer["error"].endswith(refused) and answer["hint"].startswith(HINT)
    assert stopped.stderr.startswith("error: ") and refused in stopped.stderr
    held = spool / "held" / SESSION
    kept = (held / "1.jsonl").read_bytes()
    assert kept.split(b"\n", 1)[1] == planted + b'{"kind": "stop", "text": "lost"}\n'
    reason = json.loads((held / "1.reason").read_text())
    assert reason["reason"] == "unreadable" and refused in reason["message"]
    landed = turn(binary, store, ADMIN, "fresh")
    assert landed.returncode == 0, landed.stderr
    assert json.loads(landed.stdout)["turn"] == 1
    log = binary.directory / "store" / "conversations" / SESSION / "turns.jsonl"
    rows = [json.loads(row) for row in log.read_text().splitlines()]
    assert [row["messages"][0]["content"] for row in rows] == ["fresh"]


def test_a_paused_span_is_discarded_when_another_workspace_changes_the_policy(
    binary: Binary,
) -> None:
    original = capture_profile("local")
    history = local(binary, original)
    first = event(binary, "UserPromptSubmit", prompt="pending-private")["session_id"]
    spool = binary.home / "profile-spool"
    # A pause interrupted after it published its fence and its marker.
    (spool / (first[-64:] + ".capture-fence")).touch(mode=0o600)
    (spool / f"{first}.paused").touch(mode=0o600)
    install(binary, original.replace("11111111", "bbbbbbbb"))
    second = event(binary, "UserPromptSubmit", prompt="new workspace")
    assert second["session_id"] != first and second["disposition"] == "spooled"
    assert not (spool / f"{first}.jsonl").exists()
    held: list[Path] = (
        list((spool / "held").rglob("*")) if (spool / "held").exists() else []
    )
    assert held == []
    assert b"pending-private" not in contents(spool)
    assert not history.exists()
