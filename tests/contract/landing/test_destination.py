"""A refusing destination waits or is blocked, and resumes (DESIGN §40.54
items 17.3 to 17.5 and 23; the T1e1e slice of the T1e1 plan, ruling D9 and
owner decision O10). A `Retry-After` of 1 s is waited and the part asked
again; one of 11 s, a 429 without one, a 503 and a dropped connection make
the destination wait, and no hook or lander asks it before the wait ends;
`failing_since` lasts across runs until a turn lands. 413
`storage_exceeded`, 403 and 401 block it: later Stops keep queueing and
nothing reaches it until `neosian record retry`, a refresh that brings a
changed machine document (a 304 does not), a successful login for a
credential block (a storage block stays), or one part at the first hook
event six hours after the refusal, whose answer restarts the six hours or
lands the rest. Records are planted with past times by rewriting the ones
the lander wrote; every wait on a background lander is a poll of an end
state with a deadline."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, Serve, slots_held
from tests.contract.landing.lander import (
    enable,
    hook,
    land,
    pending,
    plant,
    prompts,
    queue,
    records,
    retry,
    served_root,
)
from tests.contract.landing.stub import Rule, Stub, forwarding, route_is
from tests.contract.landing.test_lander import until
from tests.contract.profile_support import run
from tests.contract.support import Binary, Server
from tests.contract.test_machine_login import (
    SECRET,
    TRUST,
    Host,
    headless,
    host_server,
    record as managed,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")
WRITE = "conversation/write_records"
SIX_HOURS = timedelta(hours=6)


@pytest.fixture
def remote(binary: Binary, port: int, serve_on: Serve) -> Iterator[tuple[Server, Stub]]:
    enable(binary, served_root(binary))
    with serve_on(port) as server, forwarding(server.url) as stub:
        yield server, stub


def refusal(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": {}}}


def parts(stub: Stub) -> int:
    return len([e for e in stub.records() if e.body["records"]])


def is_part(exchange: Any) -> bool:
    return bool(exchange.route == WRITE and exchange.body["records"])


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def stamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def backdate(binary: Binary, change: Callable[[dict[str, Any]], None]) -> None:
    """Rewrites the home's one destination record with `change`."""
    [path] = sorted((binary.home / "spool" / "landing").glob("*.json"))
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record) + "\n")


def wait_over(record: dict[str, Any]) -> None:
    record["wait_until"] = stamp(datetime.now(UTC) - timedelta(seconds=1))


def six_hours_ago(record: dict[str, Any]) -> None:
    record["block"]["next_attempt"] = stamp(datetime.now(UTC) - timedelta(seconds=1))
    record["block"]["since"] = stamp(datetime.now(UTC) - SIX_HOURS)


def waits_about(record: dict[str, Any], seconds: int) -> None:
    left = moment(record["wait_until"]) - datetime.now(UTC)
    assert timedelta(seconds=seconds - 5) < left <= timedelta(seconds=seconds), record


def stop(binary: Binary, url: str, session: str, prompt: str) -> None:
    """A turn of `prompt` closed by a Stop that is free to start a lander."""
    plant(binary, session, prompt)
    stopped = hook(
        binary, url, session, "Stop", starts=True, last_assistant_message="done"
    )
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert json.loads(stopped.stdout)["disposition"] == "pending"


def session_start(binary: Binary, url: str, session: str) -> None:
    started = hook(binary, url, session, "SessionStart", starts=True, source="startup")
    assert started.returncode == 0, started.stdout + started.stderr


def landings(stub: Stub) -> int:
    """The requests that land a turn (a hook's own reads are not counted)."""
    routes = (WRITE, "conversation/append_turn", "conversation/read_records")
    return len([e for e in stub.log if e.route in routes])


def test_a_retry_after_of_1_second_is_waited_and_the_turn_lands(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "destination-retry-1"
    queue(binary, hold_lock, stub.url, session, "throttled once")
    rule = Rule(when=is_part, kind="answer", status=429, body="")
    rule.headers = {"Retry-After": "1"}
    stub.add(rule)
    summary = land(binary)
    assert (summary["landed"], summary["stopped"]) == (1, [])
    [refused, again] = [e for e in stub.records() if e.body["records"]]
    assert (refused.status, again.status) == (429, 200)
    assert refused.body["records"] == again.body["records"]
    assert prompts(server, session) == ["throttled once"] and records(binary) == []


def test_a_retry_after_of_11_seconds_waits_and_no_lander_asks_before_it_ends(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "destination-retry-11"
    queue(binary, hold_lock, stub.url, session, "one")
    rule = Rule(when=is_part, kind="answer", status=429, body="")
    rule.headers = {"Retry-After": "11"}
    stub.add(rule)
    assert land(binary)["stopped"][0]["message"] == "the destination answered 429"
    [record] = records(binary)
    assert record["block"] is None and record["failing_since"] is None
    waits_about(record, 11)
    asked = landings(stub)
    # Stop and SessionStart start no lander for it; a later Stop still queues.
    stop(binary, stub.url, session, "two")
    session_start(binary, stub.url, session)
    assert land(binary)["skipped"] == [session]
    assert landings(stub) == asked
    assert pending(binary, session) == [1, 2]
    # Once the wait is over, the next hook event starts one that lands both.
    backdate(binary, wait_over)
    stop(binary, stub.url, session, "three")
    until(lambda: len(prompts(server, session)) == 3, "the queue landed")
    assert prompts(server, session) == ["one", "two", "three"]
    until(lambda: records(binary) == [], "the wait ended")


def test_a_429_without_retry_after_waits_60_seconds(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    _, stub = remote
    queue(binary, hold_lock, stub.url, "destination-429", "throttled")
    stub.add(Rule(when=is_part, kind="answer", status=429, body=""))
    land(binary)
    [record] = records(binary)
    waits_about(record, 60)
    assert record["failing_since"] is None


@pytest.mark.parametrize("fault", ["503", "dropped connection"])
def test_a_fault_waits_60_seconds_and_its_start_lasts_until_a_turn_lands(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub], fault: str
) -> None:
    server, stub = remote
    session = "destination-fault"
    queue(binary, hold_lock, stub.url, session, "after faults")

    def failing() -> Rule:
        if fault == "503":
            return Rule(when=is_part, kind="answer", status=503, body="down")
        return Rule(when=is_part, kind="cut")

    stub.add(failing())
    land(binary)
    [first] = records(binary)
    waits_about(first, 60)
    assert first["failing_since"] is not None and first["block"] is None
    backdate(binary, wait_over)
    stub.add(failing())
    stub.next_run()
    land(binary)
    [second] = records(binary)
    assert second["failing_since"] == first["failing_since"]
    waits_about(second, 60)
    backdate(binary, wait_over)
    stub.next_run()
    assert land(binary)["landed"] == 1
    assert records(binary) == [] and prompts(server, session) == ["after faults"]


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (413, "storage_exceeded", "storage"),
        (403, "forbidden", "forbidden"),
        (401, "unauthorized", "credential"),
    ],
)
def test_a_refusal_of_writes_blocks_later_stops_queue_and_nothing_reaches_it(
    binary: Binary,
    hold_lock: Hold,
    remote: tuple[Server, Stub],
    status: int,
    code: str,
    reason: str,
) -> None:
    _, stub = remote
    session = "destination-blocked"
    queue(binary, hold_lock, stub.url, session, "one")
    body = refusal(code, "refused here")
    stub.add(Rule(when=is_part, kind="answer", status=status, body=body, times=99))
    land(binary)
    [record] = records(binary)
    block = record["block"]
    assert (block["reason"], block["status"], block["code"]) == (reason, status, code)
    assert block["message"] == f"[{code}] refused here"
    assert moment(block["next_attempt"]) - moment(block["since"]) == SIX_HOURS
    assert record["host"] == stub.url.removeprefix("http://")
    asked = landings(stub)
    stop(binary, stub.url, session, "two")
    session_start(binary, stub.url, session)
    assert land(binary)["skipped"] == [session]
    assert landings(stub) == asked
    assert pending(binary, session) == [1, 2]


def test_record_retry_ends_a_block_and_names_the_destination(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "destination-retry"
    queue(binary, hold_lock, stub.url, session, "retried")
    stub.add(
        Rule(when=is_part, kind="answer", status=403, body=refusal("forbidden", "no"))
    )
    land(binary)
    host = stub.url.removeprefix("http://")
    plain = binary.run("record", "retry")
    assert plain.returncode == 0, plain.stderr
    assert plain.stdout == (
        f"{host}: was blocked (forbidden); its turns land at the next hook event\n"
    )
    assert binary.run("record", "retry").stdout == (
        "No destination was waiting or blocked.\n"
    )
    stub.next_run()
    assert land(binary)["landed"] == 1
    assert prompts(server, session) == ["retried"]
    queue(binary, hold_lock, stub.url, session, "waited")
    stub.add(Rule(when=is_part, kind="answer", status=503, body="down"))
    land(binary)
    [record] = records(binary)
    assert retry(binary) == [
        {"host": host, "blocked": None, "wait_until": record["wait_until"]}
    ]


def test_a_block_six_hours_old_gets_one_part_at_the_next_hook_event(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "destination-six-hours"
    queue(binary, hold_lock, stub.url, session, "one")
    queue(binary, hold_lock, stub.url, session, "two")
    stub.add(
        Rule(when=is_part, kind="answer", status=403, body=refusal("forbidden", "no"))
    )
    land(binary)
    [blocked] = records(binary)
    # Six hours on, one part. Refused (by an answer that would hold the
    # turn outside the attempt), the turn stays and the six hours restart.
    backdate(binary, six_hours_ago)
    before = parts(stub)
    bad = refusal("value_error", "still refused")
    stub.add(Rule(when=is_part, kind="answer", status=400, body=bad))
    session_start(binary, stub.url, session)

    def message() -> Any:
        found = records(binary)
        return found and found[0]["block"]["message"]

    until(lambda: message() == "[value_error] still refused", "the attempt answered")
    [again] = records(binary)
    assert parts(stub) - before == 1
    assert pending(binary, session) == [1, 2]
    assert not (binary.home / "spool" / "held").exists()
    assert again["block"]["since"] != blocked["block"]["since"]
    left = moment(again["block"]["next_attempt"]) - datetime.now(UTC)
    assert SIX_HOURS - timedelta(minutes=1) < left <= SIX_HOURS
    # Taken: the block ends and the rest lands.
    backdate(binary, six_hours_ago)
    session_start(binary, stub.url, session)
    until(lambda: len(prompts(server, session)) == 2, "the queue landed")
    assert prompts(server, session) == ["one", "two"]
    until(lambda: records(binary) == [], "the block ended")


def lander(binary: Binary) -> Any:
    landed = run(
        binary,
        "record-land",
        "--home",
        str(binary.home),
        env={**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET},
    )
    assert landed.returncode == 0, landed.stdout + landed.stderr
    return json.loads(landed.stdout)


def managed_turn(binary: Binary, prompt: str) -> None:
    """A managed profile's turn closed by its Stop while no lander starts."""
    with slots_held(binary.home):
        opened = managed(binary, SECRET, "UserPromptSubmit", prompt=prompt)
        assert opened.returncode == 0, opened.stderr
        stopped = managed(binary, SECRET, "Stop", last_assistant_message="r")
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(stopped.stdout)["disposition"] == "pending"


def blocks(binary: Binary) -> list[str]:
    return [r["block"]["reason"] for r in records(binary) if r["block"]]


def refresh(binary: Binary) -> Any:
    refreshed = run(
        binary,
        "login",
        "--refresh",
        "--json",
        env={**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET},
    )
    assert refreshed.returncode == 0, refreshed.stderr
    return json.loads(refreshed.stdout)


def blocked_host(binary: Binary, host: Host) -> None:
    """The host's state service has no records and no `append_turn`: its 404
    blocks the destination as a redirect does."""
    managed_turn(binary, "kept")
    lander(binary)
    assert blocks(binary) == ["forbidden"]
    assert records(binary)[0]["host"] == host.url.removeprefix("https://")


def test_a_refresh_that_brings_a_changed_document_unblocks_and_a_304_does_not(
    binary: Binary,
) -> None:
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        blocked_host(binary, host)
        assert refresh(binary)["updated"] is False
        assert blocks(binary) == ["forbidden"]
        host.etag = '"2"'
        assert refresh(binary)["updated"] is True
        assert blocks(binary) == []


def test_a_login_ends_a_credential_block_and_leaves_a_storage_block(
    binary: Binary,
) -> None:
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        managed_turn(binary, "kept")
        host.tokens = ()
        lander(binary)
        [record] = records(binary)
        assert record["block"]["reason"] == "credential"
        assert "run neosian login" in record["block"]["message"]
        host.tokens = (SECRET,)
        assert headless(binary, host).returncode == 0
        assert blocks(binary) == []
        # The same document again: a storage block outlives the login.
        lander(binary)
        backdate(binary, lambda record: record["block"].update(reason="storage"))
        assert headless(binary, host).returncode == 0
        assert blocks(binary) == ["storage"]


def test_the_lander_reports_a_destination_it_skips_for_its_record(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    """A planted wait whose time has not come keeps a foreground lander away."""
    _, stub = remote
    session = "destination-planted"
    queue(binary, hold_lock, stub.url, session, "planted")
    stub.add(Rule(when=route_is(WRITE), kind="answer", status=503, body="down"))
    land(binary)
    backdate(
        binary, lambda r: r.update(wait_until=stamp(datetime.now(UTC) + SIX_HOURS))
    )
    asked = landings(stub)
    stub.next_run()
    assert land(binary)["skipped"] == [session]
    assert landings(stub) == asked
    paths = list(Path(binary.home / "spool" / "landing").glob("*.json"))
    assert len(paths) == 1 and len(paths[0].stem) == 32
