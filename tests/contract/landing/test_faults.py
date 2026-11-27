"""A destination's faults are not charged to its turns, and a first turn
records cannot carry leaves later documents written (rulings E15 and E16 of
the T1e1 plan); `neosian record-land` reports a queue it cannot list and
serves a `--spool DIR` tree. Two forwarding stubs in front of one `neosian
serve` are two destinations. Expected values are literal or computed here."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, Serve
from tests.contract.landing.lander import (
    enable,
    hook,
    land,
    lander,
    pending,
    queue,
    rows,
    served_root,
    turns,
)
from tests.contract.landing.stub import Rule, Stub, forwarding, route_is
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")
WRITE = "conversation/write_records"
APPEND = "conversation/append_turn"


@pytest.fixture
def remote(binary: Binary, port: int, serve_on: Serve) -> Iterator[tuple[Server, Stub]]:
    enable(binary, served_root(binary))
    with serve_on(port) as server, forwarding(server.url) as stub:
        yield server, stub


def without_records(capabilities: Any) -> Any:
    capabilities.pop("turn_records", None)
    return capabilities


def contents(server: Server, session: str) -> list[str]:
    return [t["messages"][0]["content"] for t in turns(server, session)]


@pytest.mark.parametrize("fault", ["configuration", "404 on append_turn"])
def test_a_destination_s_fault_keeps_its_turns_and_another_destination_lands(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub], fault: str
) -> None:
    server, stub = remote
    first, second, elsewhere = "faults-a1", "faults-a2", "faults-b"
    with forwarding(server.url) as other:
        queue(binary, hold_lock, stub.url, first, "first")
        queue(binary, hold_lock, stub.url, second, "second")
        queue(binary, hold_lock, other.url, elsewhere, "elsewhere")
        if fault == "configuration":
            message = "this store does not keep turn records"
            error: dict[str, Any] = {"code": "agent_configuration_error"}
            error |= {"message": message, "details": {}}
            body = {"error": error}
            parts = Rule(
                when=lambda e: e.route == WRITE and bool(e.body["records"]),
                kind="answer",
                status=400,
                body=body,
            )
            stub.add(parts)
            told = f"[agent_configuration_error] {message}"
        else:
            handshakes = Rule(
                when=route_is("capabilities"),
                kind="rewrite",
                rewrite=without_records,
                times=99,
            )
            stub.add(handshakes)
            stub.add(Rule(when=route_is(APPEND), kind="answer", status=404, body=""))
            told = "the destination answered 404"
        summary = land(binary)
        assert summary["stopped"] == [{"conversation_id": first, "message": told}]
        assert summary["skipped"] == [second]
        assert (summary["landed"], summary["held"], summary["faults"]) == (1, 0, [])
        assert pending(binary, first) == [1] and pending(binary, second) == [1]
        assert not (binary.home / "spool" / "held").exists()
        sent = [e.body["conversation_id"] for e in stub.log if e.body]
        assert second not in sent
        if fault == "configuration":
            assert len([e for e in stub.log if e.route == "capabilities"]) == 2
        assert turns(server, first) == [] and contents(server, elsewhere) == [
            "elsewhere"
        ]


def test_a_first_turn_records_cannot_carry_leaves_later_documents_written(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "faults-long-id"
    spool = binary.home / "spool"
    spool.mkdir(parents=True)
    long_call: dict[str, Any] = {"kind": "tool", "id": "i" * 1_025, "name": "Bash"}
    long_call |= {"input": {}, "response": "ok"}
    for prompt, extra in [("first", [long_call]), ("second", [])]:
        lines = [{"kind": "prompt", "text": prompt}, *extra]
        span = "".join(json.dumps(line) + "\n" for line in lines)
        (spool / f"{session}.jsonl").write_text(span)
        stopped = hook(binary, stub.url, session, "Stop", last_assistant_message="ok")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert json.loads(stopped.stdout)["disposition"] == "recorded"
    whole, records = rows(binary, session)
    assert "messages" in whole and records["neosian_format"] == 2
    reads = [e for e in stub.log if e.route.startswith("conversation/read")]
    # Each document: the header read refused (turn 1 cannot be records),
    # then turn 1 read whole (ruling E16).
    assert [(e.route, e.status) for e in reads] == [
        ("conversation/read_records", 400),
        ("conversation/read_turns", 200),
    ] * 2
    versions = server.post(
        "memory/versions", scope="user:contract", path=f"sessions/{session}"
    )["versions"]
    assert "- turns: 2" in versions[0]["content"]
    assert contents(server, session) == ["first", "second"]


def test_record_land_serves_a_spool_tree_and_reports_one_it_cannot_list(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    tree = binary.directory / "replay-spool"
    queue(binary, hold_lock, stub.url, "faults-tree", "from a tree", tree=tree)
    landed = lander(binary, tree)
    assert landed.returncode == 0, landed.stderr
    assert json.loads(landed.stdout)["landed"] == 1
    assert contents(server, "faults-tree") == ["from a tree"]
    broken = binary.directory / "broken-spool"
    broken.mkdir()
    (broken / "pending").write_text("not a directory")
    failed = lander(binary, broken)
    assert failed.returncode == 1
    summary = json.loads(failed.stdout)
    error = "Not a directory (os error 20)"
    assert summary["faults"] == [{"tree": str(broken), "error": error}]
