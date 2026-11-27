"""A lander's admission of a queued turn (rulings G1 and G2 of the T1e1 plan),
driven at the forwarding stub's hold gates. A pause given while a part is in
flight, then a resume and a new turn closed: the new turn takes a number the
held lander never had, and that lander's late answer neither rewrites, sends
nor removes it. A pause given while an `append_turn` is answered with a short
`Retry-After` sends nothing more after the wait. A profile whose capture is
turned off while a part is in flight is sent no further part."""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import Serve, slot_files, slots_held
from tests.contract.landing.lander import draft_id, enable, plant, popen, turns
from tests.contract.landing.stub import Exchange, Gate, Rule, forwarding, route_is
from tests.contract.landing.test_lander import LARGE, WRITE, stop, until
from tests.contract.profile_support import PREFIX, PROFILE, bind, grant_env, install
from tests.contract.support import ADMIN, Binary, serve
from tests.contract.test_profile_capture import event as profile_event

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="setsid and flock")
HISTORY = '\n[history]\nsource = "knowledge"\nscope = "workspace:portable"'
ENV = {"CAPTURE_TOKEN": ADMIN}


def profiled(binary: Binary, url: str, capture: str = "shared") -> None:
    """The portable profile, its history kept by the server behind `url`."""
    install(
        binary,
        PROFILE + HISTORY + f'\nprefix = "{PREFIX}"\n[capture]\nmode = "{capture}"\n',
    )
    bind(binary, "--url", url, "--token-env", "CAPTURE_TOKEN", "--capture", "shared")


def first_part(exchange: Exchange) -> bool:
    body = exchange.body
    return exchange.route == WRITE and bool(body["records"]) and not body.get("seal")


@contextmanager
def free_slots_held(home: Path) -> Iterator[None]:
    """Every landing slot no lander holds, held: a hook run inside starts no
    lander while one runs."""
    with ExitStack() as stack:
        for slot in slot_files(home):
            handle = stack.enter_context(slot.open("a+b"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
        yield


def lander(binary: Binary) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(binary.executable), "record-land", "--home", str(binary.home)],
        env={**binary.environment(), **ENV},
        cwd=binary.directory,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def summary(process: subprocess.Popen[str]) -> Any:
    out, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
    return json.loads(out)


def numbers(directory: Path) -> list[int]:
    return sorted(int(path.stem) for path in directory.glob("*.jsonl"))


def queued_profile_turn(binary: Binary, prompt: str) -> str:
    """A large profile turn closed and left queued; its conversation."""
    with slots_held(binary.home):
        opened = profile_event(binary, "UserPromptSubmit", env=ENV, prompt=prompt)
        closed = profile_event(binary, "Stop", env=ENV, last_assistant_message="a")
    assert closed["disposition"] == "pending"
    return str(opened["session_id"])


def stored(server: Any, conversation: str) -> list[str]:
    answer = server.post("conversation/read_turns", conversation_id=conversation)
    return [turn["messages"][0]["content"] for turn in answer["turns"]]


def test_a_turn_closed_after_a_pause_is_never_touched_by_the_lander_of_the_old_one(
    binary: Binary,
) -> None:
    enable(binary, binary.home)
    grants = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=grants) as server, forwarding(server.url) as stub:
        profiled(binary, stub.url)
        conversation = queued_profile_turn(binary, "old " * (LARGE // 4))
        pending = binary.home / "profile-spool" / "pending" / conversation
        line = (pending / "1.jsonl").read_text().split("\n", 1)[0]
        old = draft_id(conversation, line)
        gate = Gate()
        stub.add(Rule(when=first_part, kind="hold", gate=gate))
        held = lander(binary)
        until(gate.reached.is_set, "the old turn's first part held at the stub")
        with free_slots_held(binary.home):
            paused = binary.json("record", "pause", conversation)
            assert paused["disposition"] == "capture_paused"
            resumed = binary.json("record", "resume", conversation)
            assert resumed["disposition"] == "capture_resumes_on_prompt"
            profile_event(binary, "UserPromptSubmit", env=ENV, prompt="after the pause")
            closed = profile_event(binary, "Stop", env=ENV, last_assistant_message="b")
            assert closed["disposition"] == "pending"
        # The held lander may still hold turn 1: the new turn is 2.
        assert numbers(pending) == [2]
        state = (pending / "2.state").read_text()
        gate.release()
        ran = summary(held)
        assert (ran["landed"], ran["paused"]) == (0, [conversation])
        sent = [e.body for e in stub.records(conversation) if e.body["draft"] == old]
        parts = [b for b in sent if b["records"]]
        assert len(parts) == 1 and not parts[0].get("seal"), "one part, then none"
        assert sent[-1].get("discard") is True
        assert numbers(pending) == [2] and (pending / "2.state").read_text() == state
        assert not (pending / "1.state").exists()
        assert stored(server, conversation) == []
        # The new turn is whole, and lands alone.
        stub.next_run()
        assert summary(lander(binary))["landed"] == 1
        assert stored(server, conversation) == ["after the pause"]
        assert not pending.exists()


def test_a_pause_during_a_retry_after_wait_sends_the_turn_no_more(
    binary: Binary, port: int, serve_on: Serve
) -> None:
    session = "admission-retry"
    with serve_on(port) as server, forwarding(server.url) as stub:
        plant(binary, session, "throttled, then paused")
        stop(binary, stub.url, session)
        gate = Gate()
        throttled = {
            "error": {"code": "rate_limited", "message": "slow", "details": {}}
        }
        stub.add(
            Rule(
                when=route_is("conversation/append_turn"),
                kind="answer",
                status=429,
                body=throttled,
                headers={"Retry-After": "1"},
                gate=gate,
            )
        )
        held = popen(binary)
        until(gate.reached.is_set, "the append held at the stub")
        # What `record pause` publishes first, before the 429 is answered.
        (binary.home / "spool" / f"{session}.paused").write_text("")
        gate.release()
        ran = summary(held)
        assert (ran["landed"], ran["paused"]) == (0, [session])
        appends = [e for e in stub.log if e.route == "conversation/append_turn"]
        assert len(appends) == 1, "nothing was sent after the wait"
        assert turns(server, session) == []
        assert not list((binary.home / "spool" / "pending" / session).glob("*.jsonl"))


def test_capture_turned_off_while_a_part_is_in_flight_sends_no_further_part(
    binary: Binary,
) -> None:
    enable(binary, binary.home)
    grants = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=grants) as server, forwarding(server.url) as stub:
        profiled(binary, stub.url)
        conversation = queued_profile_turn(binary, "kept " * (LARGE // 5))
        gate = Gate()
        stub.add(Rule(when=first_part, kind="hold", gate=gate))
        held = lander(binary)
        until(gate.reached.is_set, "the first part held at the stub")
        profiled(binary, stub.url, capture="off")
        gate.release()
        ran = summary(held)
        assert ran["landed"] == 0
        [stopped] = ran["stopped"]
        assert stopped["conversation_id"] == conversation
        assert "capture is off" in stopped["message"]
        parts = [e.body for e in stub.records(conversation) if e.body["records"]]
        assert len(parts) == 1 and not any(b.get("seal") for b in parts)
        pending = binary.home / "profile-spool" / "pending" / conversation
        assert numbers(pending) == [1]
        assert stored(server, conversation) == []
