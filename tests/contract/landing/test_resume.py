"""A lander killed mid-turn resumes without resending an accepted part
(DESIGN §40.54 item 17.3; rulings D11 and E9 of the T1e1 plan).
`neosian record-land --home H` runs in the foreground and is killed with
SIGKILL while the forwarding stub holds one of its requests: a part held
before the server applied it and one held after, then twenty requests
chosen by a seeded generator. The next run probes and continues from the
server's `next_seq`, repeating only record `next_seq - 1` (E9's check), and
every queued turn is stored exactly once, in order. `LANDING_SEED=<n>`
replays a random run; the seed is printed with every run."""

from __future__ import annotations

import os
import random
import sys
import time
from collections.abc import Callable, Iterator

import pytest

from tests.contract.landing.conftest import Hold, Serve
from tests.contract.landing.lander import (
    PART_BYTES,
    draft_id,
    enable,
    land,
    opening,
    pending,
    popen,
    prompts,
    queue,
    rows,
    served_root,
    state,
    turns,
)
from tests.contract.landing.stub import (
    Exchange,
    Gate,
    Rule,
    Stub,
    forwarding,
    resumed_without_resending,
)
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL and flock")
WRITE = "conversation/write_records"
KILLS = 20


@pytest.fixture
def remote(binary: Binary, port: int, serve_on: Serve) -> Iterator[tuple[Server, Stub]]:
    enable(binary, served_root(binary))
    with serve_on(port) as server, forwarding(server.url) as stub:
        yield server, stub


def kill_at(
    binary: Binary, stub: Stub, when: Callable[[Exchange], bool], after: bool
) -> bool:
    """Runs the lander, holds the first request `when` matches (before or
    after the server applied it) and kills the lander while it waits for
    the answer. False when the run ended before such a request."""
    gate = Gate()
    kind = "hold_after" if after else "hold_before"
    rule = stub.add(Rule(when=when, kind=kind, gate=gate))
    stub.next_run()
    process = popen(binary)
    deadline = time.monotonic() + 30
    try:
        while not gate.reached.wait(0.005):
            if process.poll() is not None:
                return False
            assert time.monotonic() < deadline, "the lander neither ended nor was held"
        process.kill()
        process.wait(10)
        return True
    finally:
        rule.times = 0
        gate.release()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def numbered(number: int) -> Callable[[Exchange], bool]:
    """The request `number` of a run, whatever its route."""
    return lambda exchange: exchange.number == number


@pytest.mark.parametrize("after", [False, True], ids=["before", "after"])
def test_a_lander_killed_while_a_part_is_held_resumes_without_resending(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub], after: bool
) -> None:
    server, stub = remote
    session = "resume-held"
    n = queue(binary, hold_lock, stub.url, session, "three parts", 2_000_000)
    draft = draft_id(session, opening(binary, session, n))

    def second(e: Exchange) -> bool:
        if e.route != WRITE or e.body["draft"] != draft:
            return False
        records = e.body["records"]
        return bool(records) and records[0]["seq"] > 0

    assert kill_at(binary, stub, second, after)
    [first, killed] = [e for e in stub.records(session) if e.body["records"]]
    confirmed = first.body["records"][-1]["seq"] + 1
    assert state(binary, session, n)["next_seq"] == confirmed
    assert killed.forwarded is after
    applied = killed.body["records"][-1]["seq"] + 1 if after else confirmed
    before = len(stub.log)
    stub.next_run()
    assert land(binary)["landed"] == 1
    resumed = [e for e in stub.log[before:] if e.route == WRITE]
    assert resumed[0].body["records"] == [] and resumed[0].answer["next_seq"] == applied
    # E9: the first part after the probe repeats record `next_seq - 1` only.
    assert resumed[1].body["records"][0]["seq"] == applied - 1
    assert "turn_bytes" not in resumed[1].body
    sent = [r["seq"] for e in stub.records(session) for r in e.body["records"]]
    repeated = sorted({seq for seq in sent if sent.count(seq) > 1})
    # After: only E9's record. Before: E9's record and the part never applied.
    unapplied = range(confirmed, killed.body["records"][-1]["seq"] + 1)
    expected = [applied - 1] if after else [applied - 1, *unapplied]
    assert repeated == expected
    assert all(len(e.raw) <= PART_BYTES for e in stub.log)
    resumed_without_resending(stub.log)
    [turn] = turns(server, session)
    assert len(turn["messages"][2]["content"]) == 2_000_000
    assert rows(binary, session)[0]["records"] == len(set(sent))


def test_twenty_kills_at_seeded_random_requests_end_with_each_turn_stored_once(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    seed = int(
        os.environ.get("LANDING_SEED") or random.SystemRandom().randrange(1 << 32)
    )
    print(f"LANDING_SEED={seed}")
    chosen = random.Random(seed)
    sessions = [f"kill-{letter}" for letter in "abcdef"]
    for session in sessions:
        for turn in range(3):
            queue(binary, hold_lock, stub.url, session, f"{session} {turn}", 1_050_000)
    kills, points = 0, []
    while kills < KILLS:
        number, after = chosen.randint(1, 8), chosen.random() < 0.5
        points.append((number, after))
        if not kill_at(binary, stub, numbered(number), after):
            break
        kills += 1
    print(f"kill points (request of the run, after the server applied it): {points}")
    assert kills == KILLS, f"the queue drained after {kills} kills (seed {seed})"
    stub.next_run()
    summary = land(binary)
    assert summary["stopped"] == [] and summary["held"] == 0, summary
    for session in sessions:
        assert prompts(server, session) == [f"{session} {turn}" for turn in range(3)]
        assert pending(binary, session) == []
    assert all(len(e.raw) <= PART_BYTES for e in stub.log)
    resumed_without_resending(stub.log)
