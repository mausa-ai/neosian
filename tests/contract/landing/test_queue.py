"""A stopped turn waits in a queue (DESIGN §40.54 items 17.1, 17.2 and 17.6,
#447; rulings D1, D2, D3, D5, D6, D11, O1 and O2 of the T1e1 plan). At Stop
the span is closed into `pending/<id>/<n>.jsonl` with its `<n>.state`; a
turn behind an older queued one, or for a URL, waits for `neosian
record-land`, which lands the conversation's queue in order, each turn as
one `append_turn`. A turn recorded under another policy is held with
`policy_changed` while later turns land. Every hook here runs with both
landing slots held, so no lander starts by itself, and the lander runs in
the foreground. Expected values are literal or computed here from the
files."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, Serve, slots_held
from tests.contract.profile_support import install, run
from tests.contract.support import ADMIN, Binary
from tests.contract.test_profile_capture import (
    capture_profile,
    contents,
    event as profile_event,
)
from tests.contract.test_profile_pause import local

SESSION = "landing-session"
STATE_KEYS = [
    "draft",
    "next_seq",
    "fingerprint",
    "destination",
    "attempt",
    "closed_at",
    "bytes",
    "agent",
    "actor",
    "session",
    "conversation_id",
    "scope",
    "mount",
    "provenance",
    "parent",
    "project",
]
CLOSED_AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")
CHANGED = {"reason": "policy_changed"}
# A policy change that keeps every text field: a replacement is added.
CHANGE = 'replacements = [{from = "SCRUB", to = "[removed]"}]\n'


def draft_id(conversation: str, line: str) -> str:
    """`p` and 32 hex digits of SHA-256 over the id, a line feed, the line."""
    return "p" + hashlib.sha256(f"{conversation}\n{line}".encode()).hexdigest()[:32]


def credential(url: str, token: str) -> str:
    """SHA-256 of the client's URL (trailing `/` trimmed) and the token, as
    one compact JSON array."""
    both = json.dumps([url.rstrip("/"), token], separators=(",", ":"))
    return hashlib.sha256(both.encode()).hexdigest()


def event(binary: Binary, name: str, **fields: Any) -> Any:
    """A profile hook event, with the home's landing slots held."""
    with slots_held(binary.home):
        return profile_event(binary, name, **fields)


def direct(
    binary: Binary, url: str, name: str, **payload: str
) -> subprocess.CompletedProcess[str]:
    with slots_held(binary.home):
        return subprocess.run(
            [str(binary.executable), "record", "--url", url]
            + ["--scope", "user:contract", "--json"],
            input=json.dumps(
                {"session_id": SESSION, "hook_event_name": name, **payload}
            ),
            env={**binary.environment(), "NEOSIAN_CLIENT_TOKEN": ADMIN},
            cwd=binary.directory,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def land(binary: Binary) -> Any:
    """`neosian record-land --home H` in the foreground: its summary."""
    landed = subprocess.run(
        [str(binary.executable), "record-land", "--home", str(binary.home)],
        env={**binary.environment(), "NEOSIAN_CLIENT_TOKEN": ADMIN},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert landed.returncode == 0, landed.stdout + landed.stderr
    return json.loads(landed.stdout)


def stopped(binary: Binary, url: str, prompt: str) -> subprocess.CompletedProcess[str]:
    """A prompt, then the Stop whose result is returned."""
    opened = direct(binary, url, "UserPromptSubmit", prompt=prompt)
    assert opened.returncode == 0, opened.stderr
    return direct(binary, url, "Stop", last_assistant_message=f"{prompt} done")


def lines(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_turns_wait_in_order_and_land_as_separate_turns(
    binary: Binary, port: int, serve_on: Serve
) -> None:
    url = f"http://127.0.0.1:{port}"
    for prompt in ["one", "two"]:
        # Nothing listens: the Stop does not try, it queues and exits 0.
        queued = stopped(binary, url, prompt)
        assert queued.returncode == 0, queued.stderr
        assert json.loads(queued.stdout)["disposition"] == "pending"
    pending = binary.home / "spool" / "pending" / SESSION
    names = sorted(path.name for path in pending.iterdir())
    assert names == ["1.jsonl", "1.state", "2.jsonl", "2.state"]
    drafts = []
    for n, prompt in [(1, "one"), (2, "two")]:
        text = (pending / f"{n}.jsonl").read_text()
        opening, *records = lines(pending / f"{n}.jsonl")
        assert list(opening) == ["kind", "opened_at", "nonce"]
        assert opening["kind"] == "span" and CLOSED_AT.fullmatch(opening["opened_at"])
        assert records == [
            {"kind": "prompt", "text": prompt},
            {"kind": "stop", "text": f"{prompt} done"},
        ]
        raw = (pending / f"{n}.state").read_text()
        state = json.loads(raw)
        assert list(state) == STATE_KEYS
        assert state["draft"] == draft_id(SESSION, text.split("\n", 1)[0])
        drafts.append(state["draft"])
        assert state["destination"] == {
            "kind": "remote",
            "url": url,
            "credential_sha256": credential(url, ADMIN),
        }
        assert ADMIN not in raw
        assert state["bytes"] == len(text.encode())
        assert CLOSED_AT.fullmatch(state["closed_at"])
        assert Path(state["project"]).resolve() == binary.directory.resolve()
        assert {key: state[key] for key in STATE_KEYS[7:15]} | {
            key: state[key] for key in ("next_seq", "attempt", "fingerprint")
        } == {
            "agent": "claude-code",
            "actor": f"claude-code:{SESSION}",
            "session": SESSION,
            "conversation_id": SESSION,
            "scope": "user:contract",
            "mount": "memories",
            "provenance": None,
            "parent": None,
            "next_seq": 0,
            "attempt": 0,
            "fingerprint": None,
        }
    assert drafts[0] != drafts[1]
    with serve_on(port) as server:
        queued = stopped(binary, url, "three")
        assert queued.returncode == 0, queued.stderr
        assert json.loads(queued.stdout)["disposition"] == "pending"
        assert land(binary)["landed"] == 3
        turns = server.post("conversation/read_turns", conversation_id=SESSION)
        assert [[m["content"] for m in t["messages"]] for t in turns["turns"]] == [
            ["one", "one done"],
            ["two", "two done"],
            ["three", "three done"],
        ]
        versions = server.post(
            "memory/versions", scope="user:contract", path=f"sessions/{SESSION}"
        )["versions"]
        assert sorted(v["actor"] for v in versions) == [
            f"client:contract/claude-code:{SESSION}#{turn}" for turn in (1, 2, 3)
        ]
        assert "- turns: 3" in versions[0]["content"]
    assert not pending.exists()
    assert not (binary.home / "spool" / f"{SESSION}.jsonl").exists()


def profile_stop(binary: Binary, text: str) -> subprocess.CompletedProcess[str]:
    return run(
        binary,
        "record",
        "--profile",
        "portable",
        "--agent",
        "codex",
        "--json",
        stdin=json.dumps(
            {
                "session_id": "native-session",
                "hook_event_name": "Stop",
                "last_assistant_message": text,
            }
        ),
    )


@POSIX
def test_a_queued_turn_of_an_earlier_policy_is_held_and_later_turns_land(
    binary: Binary, hold_lock: Hold
) -> None:
    text = capture_profile("local")
    history = local(binary, text)
    conversation = event(binary, "UserPromptSubmit", prompt="earlier policy")[
        "session_id"
    ]
    spool = binary.home / "profile-spool"
    with hold_lock(spool / "pending" / conversation):
        queued = event(binary, "Stop", last_assistant_message="earlier answer")
    assert queued["disposition"] == "pending" and queued["turn"] is None
    recorded_under = json.loads(
        (spool / "pending" / conversation / "1.state").read_text()
    )
    assert recorded_under["destination"] == {
        "kind": "profile",
        "name": "portable",
        "project": str(binary.directory.resolve()),
        "source": "history",
    }
    assert re.fullmatch(r"[0-9a-f]{64}", recorded_under["fingerprint"])
    install(binary, text + CHANGE)
    event(binary, "UserPromptSubmit", prompt="later policy")
    # Behind an older queued turn: left to the lander (D6).
    queued = event(binary, "Stop", last_assistant_message="later answer")
    assert queued["disposition"] == "pending"
    assert land(binary)["landed"] == 1
    held = spool / "held" / conversation
    assert json.loads((held / "1.reason").read_text()) == CHANGED
    assert json.loads((held / "1.state").read_text()) == recorded_under
    assert b"earlier policy" in (held / "1.jsonl").read_bytes()
    stored = contents(history)
    assert b"later policy" in stored and b"earlier" not in stored
    event(binary, "UserPromptSubmit", prompt="still later")
    assert event(binary, "Stop", last_assistant_message="ok")["turn"] == 2
    assert (held / "1.jsonl").is_file()


def test_a_policy_change_while_a_span_is_open_holds_it_and_recording_goes_on(
    binary: Binary,
) -> None:
    text = capture_profile("local")
    history = local(binary, text)
    conversation = event(binary, "UserPromptSubmit", prompt="before the change")[
        "session_id"
    ]
    spool = binary.home / "profile-spool"
    (guard,) = spool.glob("*.policy")
    recorded_under = guard.read_text()
    install(binary, text + CHANGE)
    # The turn begins in the middle of the exchange: the reply alone.
    done = event(binary, "Stop", last_assistant_message="after the change")
    assert (done["disposition"], done["turn"]) == ("recorded", 1)
    held = spool / "held" / conversation
    assert json.loads((held / "1.reason").read_text()) == CHANGED
    state = json.loads((held / "1.state").read_text())
    assert list(state) == STATE_KEYS
    assert state["fingerprint"] == recorded_under
    assert [state[key] for key in ("destination", "scope", "mount", "provenance")] == [
        None
    ] * 4
    opening, *records = lines(held / "1.jsonl")
    assert state["draft"] == draft_id(
        conversation, (held / "1.jsonl").read_text().split("\n", 1)[0]
    )
    assert records == [{"kind": "prompt", "text": "before the change"}]
    stored = contents(history)
    assert b"after the change" in stored and b"before the change" not in stored
    assert not list(spool.glob("*.policy")) and not list(spool.glob("*.jsonl"))


@POSIX
def test_capture_off_leaves_queued_turns_unsent_until_it_is_on(
    binary: Binary, hold_lock: Hold
) -> None:
    text = capture_profile("local")
    history = local(binary, text)
    conversation = event(binary, "UserPromptSubmit", prompt="queued while on")[
        "session_id"
    ]
    spool = binary.home / "profile-spool"
    with hold_lock(spool / "pending" / conversation):
        assert event(binary, "Stop")["disposition"] == "pending"
    install(binary, text.replace('mode = "local"', 'mode = "off"'))
    for name in ["UserPromptSubmit", "Stop", "Stop"]:
        assert event(binary, name, prompt="off")["disposition"] == (
            "capture_off_pending"
        )
    assert not history.exists()
    assert (spool / "pending" / conversation / "1.jsonl").is_file()
    install(binary, text)
    event(binary, "UserPromptSubmit", prompt="after it is on")
    assert event(binary, "Stop", last_assistant_message="on")["disposition"] == (
        "pending"
    )
    assert land(binary)["landed"] == 2
    turns = (history / "conversations" / conversation / "turns.jsonl").read_text()
    prompts = [json.loads(row)["messages"][0]["content"] for row in turns.splitlines()]
    assert prompts == ["queued while on", "after it is on"]


@POSIX
def test_a_pause_discards_pending_turns_and_keeps_held_ones(
    binary: Binary, hold_lock: Hold
) -> None:
    text = capture_profile("local")
    history = local(binary, text)
    conversation = event(binary, "UserPromptSubmit", prompt="queued-private")[
        "session_id"
    ]
    spool = binary.home / "profile-spool"
    with hold_lock(spool / "pending" / conversation):
        assert event(binary, "Stop")["disposition"] == "pending"
    event(binary, "UserPromptSubmit", prompt="to be held")
    install(binary, text + CHANGE)
    event(binary, "UserPromptSubmit", prompt="open-private")
    held = spool / "held" / conversation
    assert json.loads((held / "2.reason").read_text()) == CHANGED
    assert binary.json("record", "pause", conversation)["disposition"] == (
        "capture_paused"
    )
    assert not list((spool / "pending" / conversation).glob("*.jsonl"))
    assert not list((spool / "pending" / conversation).glob("*.state"))
    assert not (spool / f"{conversation}.jsonl").exists()
    assert b"to be held" in (held / "2.jsonl").read_bytes()
    assert not history.exists()
    assert b"private" not in contents(spool)
