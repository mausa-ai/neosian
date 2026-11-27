"""The conversation rows of 17.4 over HTTP (DESIGN §40.54 items 15.2, 17.3
and 17.4; rulings D7, D8, D10, D11, E8 and E9 of the T1e1 plan). Turns are
queued for a URL by the real Stop hook while the test holds the
conversation's landing lock, then `neosian record-land --home H` lands them
in the foreground through a forwarding stub in front of a real `neosian
serve` on a FileStore that keeps records. The stub answers in the server's
place where a row needs it and logs every raw request. Expected values are
literal or computed here."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, Serve
from tests.contract.landing.lander import (
    PART_BYTES,
    draft_id,
    enable,
    fresh_id,
    held,
    hook,
    land,
    legacy_body,
    opening,
    pending,
    queue,
    rows,
    served_root,
    state,
    turns,
)
from tests.contract.landing.stub import (
    Exchange,
    Rule,
    Stub,
    conflict,
    forwarding,
    resumed_without_resending,
    route_is,
)
from tests.contract.support import Binary, Server

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")
WRITE = "conversation/write_records"
APPEND = "conversation/append_turn"
FORMAT = "agent_conversation_format_unsupported"
# A result of a million characters, some multi-byte: two parts.
LARGE = 1_000_000

pytestmark = POSIX


@pytest.fixture
def remote(binary: Binary, port: int, serve_on: Serve) -> Iterator[tuple[Server, Stub]]:
    enable(binary, served_root(binary))
    with serve_on(port) as server, forwarding(server.url) as stub:
        yield server, stub


def parts_of(stub: Stub, draft: str) -> list[Exchange]:
    return [e for e in stub.records() if e.body["draft"] == draft and e.body["records"]]


def part_of(draft: str) -> Any:
    return lambda e: e.route == WRITE and e.body["draft"] == draft and e.body["records"]


def refusal(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": {}}}


def document(server: Server, session: str) -> str:
    """The latest version of the conversation's sessions document."""
    versions = server.post(
        "memory/versions", scope="user:contract", path=f"sessions/{session}"
    )["versions"]
    return str(versions[0]["content"])


def without_records(capabilities: Any) -> Any:
    capabilities.pop("turn_records", None)
    return capabilities


def test_a_turn_lands_in_parts_its_total_on_the_opening_and_the_last_sealing(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-parts"
    n = queue(binary, hold_lock, stub.url, session, "build it", LARGE)
    assert stub.log == []
    draft = draft_id(session, opening(binary, session, n))
    summary = land(binary)
    assert (summary["landed"], summary["held"], summary["stopped"]) == (1, 0, [])
    parts = parts_of(stub, draft)
    assert len(parts) == 2
    assert all(len(e.raw) <= PART_BYTES for e in stub.log)
    seqs = [record["seq"] for e in parts for record in e.body["records"]]
    assert seqs == list(range(len(seqs)))
    [header] = rows(binary, session)
    assert (header["neosian_format"], header["draft"], header["records"]) == (
        2,
        draft,
        len(seqs),
    )
    assert parts[0].body["turn_bytes"] == header["bytes"] > PART_BYTES
    assert parts[0].body["actor"] == f"claude-code:{session}"
    assert "turn_bytes" not in parts[1].body and "actor" not in parts[1].body
    assert [e.body.get("seal", False) for e in parts] == [False, True]
    assert header["actor"] == f"client:contract/claude-code:{session}"
    # The first turn's time comes from its header alone (ruling D7).
    reads = [e for e in stub.log if e.route.startswith("conversation/read")]
    assert [e.raw for e in reads] == [
        b'{"conversation_id":"rows-parts","kinds":[],"page_bytes":1}'
    ]
    [turn] = turns(server, session)
    assert turn["messages"][0]["content"] == "build it"
    assert len(turn["messages"][2]["content"]) == LARGE
    assert not (binary.home / "spool" / "pending" / session).exists()


def test_a_stop_to_a_url_answers_pending_and_the_lander_lands_its_turn_as_records(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-hook"
    opened = hook(binary, stub.url, session, "UserPromptSubmit", prompt="queued")
    assert opened.returncode == 0, opened.stderr
    stopped = hook(binary, stub.url, session, "Stop", last_assistant_message="ok")
    assert stopped.returncode == 0, stopped.stderr
    envelope = json.loads(stopped.stdout)
    assert (envelope["disposition"], envelope["turn"]) == ("pending", None)
    # The hook sends no part: the turn waits for a lander (E12, D15).
    assert stub.records(session) == []
    assert pending(binary, session) == [1]
    assert land(binary)["landed"] == 1
    assert [e.body["records"] == [] for e in stub.records(session)] == [True, False]
    assert rows(binary, session)[0]["neosian_format"] == 2
    assert [t["messages"][0]["content"] for t in turns(server, session)] == ["queued"]


def test_a_lost_seal_answer_is_finished_by_the_next_run_and_stored_once(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-lost-seal"
    n = queue(binary, hold_lock, stub.url, session, "sealed once", LARGE)
    sealing = Rule(
        when=lambda e: e.route == WRITE and e.body.get("seal") is True, kind="drop"
    )
    stub.add(sealing)
    first = land(binary)
    assert first["landed"] == 0 and first["stopped"][0]["conversation_id"] == session
    assert pending(binary, session) == [n]
    assert stub.records(session)[-1].answer["state"] == "sealed"
    before = len(stub.log)
    stub.next_run()
    second = land(binary)
    assert (second["landed"], second["stopped"]) == (1, [])
    again = [e for e in stub.log[before:] if e.route == WRITE]
    assert [(e.body["records"], e.answer["state"]) for e in again] == [([], "sealed")]
    assert len(turns(server, session)) == len(rows(binary, session)) == 1
    assert not (binary.home / "spool" / "pending" / session).exists()
    assert "- turns: 1" in document(server, session)
    resumed_without_resending(stub.log)


def test_draft_sealed_on_a_seal_the_server_already_took_finishes_the_turn(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    """A real server answers `draft_sealed` to a request that follows a seal
    with new records; this lander never sends one (it probes first, and a
    sealed draft answers the one record E9 repeats with `sealed`). So the
    stub forwards the seal, which seals the draft at the server, and answers
    it as a gateway that retried the seal would: 400 `draft_sealed` carrying
    the sealed header (17.4's row: the turn is stored)."""
    server, stub = remote
    session = "rows-draft-sealed"
    queue(binary, hold_lock, stub.url, session, "sealed by a retry", LARGE)

    def retried(answer: Any) -> Any:
        return conflict(session, "draft_sealed", turn=answer["turn"])

    seal = Rule(
        when=lambda e: e.route == WRITE and e.body.get("seal") is True,
        kind="rewrite",
        rewrite=retried,
        rewrite_status=400,
    )
    stub.add(seal)
    summary = land(binary)
    assert (summary["landed"], summary["stopped"]) == (1, [])
    sent = stub.records(session)
    assert sent[-1].body.get("seal") is True and sent[-1].status == 400
    assert len(turns(server, session)) == len(rows(binary, session)) == 1
    assert "- turns: 1" in document(server, session)


@pytest.mark.parametrize("expiry", ["410", "draft_gap", "none after progress"])
def test_an_expired_draft_starts_again_from_seq_0_and_is_stored_once(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub], expiry: str
) -> None:
    server, stub = remote
    session = "rows-expired"
    n = queue(binary, hold_lock, stub.url, session, "expires", LARGE)
    draft = draft_id(session, opening(binary, session, n))

    def second(e: Exchange) -> bool:
        return bool(part_of(draft)(e)) and e.body["records"][0]["seq"] > 0

    if expiry == "none after progress":
        stub.add(Rule(when=second, kind="answer", status=503, body="down"))
        first = land(binary)
        assert first["landed"] == 0
        progress = state(binary, session, n)["next_seq"]
        assert progress > 0
        stub.discard(session, draft)
        stub.next_run()
    elif expiry == "410":
        stub.add(Rule(when=second, kind="expire", status=410, body="gone"))
    else:
        body = conflict(session, "draft_gap", next_seq=0)
        stub.add(Rule(when=second, kind="expire", status=400, body=body))
    expired = len(stub.log)
    summary = land(binary)
    assert (summary["landed"], summary["stopped"]) == (1, [])
    after = [e for e in stub.log[expired:] if e.route == WRITE]
    if expiry != "none after progress":
        # What follows the part the stub expired.
        [cut] = [i for i, e in enumerate(after) if not e.forwarded]
        assert after[cut].status in (410, 400) and after[cut].body["records"][0]["seq"]
        after = after[cut + 1 :]
    assert after[0].body["records"] == [] and after[0].answer["state"] == "none"
    assert after[1].body["records"][0]["seq"] == 0 and "turn_bytes" in after[1].body
    [turn] = turns(server, session)
    assert turn["messages"][0]["content"] == "expires"
    assert rows(binary, session)[0]["draft"] == draft
    resumed_without_resending(stub.log)


@pytest.mark.parametrize("reason", ["draft_mismatch", "draft_size"])
def test_a_draft_that_does_not_match_is_written_once_more_under_the_fresh_id(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub], reason: str
) -> None:
    server, stub = remote
    session = "rows-fresh"
    n = queue(binary, hold_lock, stub.url, session, "fresh")
    line = opening(binary, session, n)
    draft, fresh = draft_id(session, line), fresh_id(session, line)
    refused = conflict(session, reason, next_seq=0, seq=0)
    stub.add(Rule(when=part_of(draft), kind="answer", status=400, body=refused))
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (1, 0)
    sent = [e.body["draft"] for e in stub.records(session) if e.body["records"]]
    assert sent == [draft, fresh]
    discards = [e.body["draft"] for e in stub.records(session) if e.body.get("discard")]
    assert discards == [draft]
    assert rows(binary, session)[0]["draft"] == fresh
    assert [t["messages"][0]["content"] for t in turns(server, session)] == ["fresh"]


def test_a_draft_refused_again_under_the_fresh_id_is_held_and_the_next_turn_lands(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-mismatch"
    n = queue(binary, hold_lock, stub.url, session, "mismatched")
    queue(binary, hold_lock, stub.url, session, "next")
    line = opening(binary, session, n)
    ids = (draft_id(session, line), fresh_id(session, line))
    refused = conflict(session, "draft_mismatch", next_seq=0, seq=0)

    def either(e: Exchange) -> bool:
        return e.route == WRITE and e.body["draft"] in ids and bool(e.body["records"])

    # Five refusals ready: a lander that kept retrying would land the sixth.
    stub.add(Rule(when=either, kind="answer", status=400, body=refused, times=5))
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (1, 1)
    assert held(binary, session, n) == {
        "reason": "mismatch",
        "status": 400,
        "code": "agent_conversation_conflict",
        "message": f"Conversation conflict on '{session}': draft_mismatch",
    }
    mine = [e for e in stub.records(session) if e.body["draft"] in ids]
    assert [e.body["draft"] for e in mine if e.body["records"]] == list(ids)
    discards = [e.body["draft"] for e in stub.records(session) if e.body.get("discard")]
    assert discards == list(ids)
    assert [t["messages"][0]["content"] for t in turns(server, session)] == ["next"]


def test_a_draft_of_another_actor_holds_the_turn_without_a_discard(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-actor"
    n = queue(binary, hold_lock, stub.url, session, "theirs")
    queue(binary, hold_lock, stub.url, session, "mine")
    draft = draft_id(session, opening(binary, session, n))
    refused = conflict(session, "draft_actor", draft=draft)
    stub.add(Rule(when=part_of(draft), kind="answer", status=400, body=refused))
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (1, 1)
    assert held(binary, session, n) == {
        "reason": "actor_changed",
        "status": 400,
        "code": "agent_conversation_conflict",
        "message": f"Conversation conflict on '{session}': draft_actor",
    }
    assert not [e for e in stub.records(session) if e.body.get("discard")]
    assert [t["messages"][0]["content"] for t in turns(server, session)] == ["mine"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "value_error"), (413, "value_error"), (422, "unprocessable_entity")],
)
def test_a_refused_value_is_held_with_the_message_and_the_next_turn_lands(
    binary: Binary,
    hold_lock: Hold,
    remote: tuple[Server, Stub],
    status: int,
    code: str,
) -> None:
    server, stub = remote
    session = "rows-refused"
    n = queue(binary, hold_lock, stub.url, session, "refused")
    queue(binary, hold_lock, stub.url, session, "accepted")
    draft = draft_id(session, opening(binary, session, n))
    body = refusal(code, "record 1: refused here")
    stub.add(Rule(when=part_of(draft), kind="answer", status=status, body=body))
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (1, 1)
    assert held(binary, session, n) == {
        "reason": "refused",
        "status": status,
        "code": code,
        "message": "record 1: refused here",
    }
    discards = [e.body["draft"] for e in stub.records(session) if e.body.get("discard")]
    assert discards == [draft]
    assert [t["messages"][0]["content"] for t in turns(server, session)] == ["accepted"]


def test_drafts_open_keeps_that_conversation_waiting_while_another_lands(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    waits, lands = "rows-open-a", "rows-open-b"
    queue(binary, hold_lock, stub.url, waits, "waits")
    queue(binary, hold_lock, stub.url, lands, "lands")
    refused = conflict(waits, "drafts_open", limit=16)
    sixteen = Rule(
        when=lambda e: e.route == WRITE
        and e.body["conversation_id"] == waits
        and bool(e.body["records"]),
        kind="answer",
        status=400,
        body=refused,
    )
    stub.add(sixteen)
    summary = land(binary)
    message = (
        f"[agent_conversation_conflict] Conversation conflict on '{waits}': drafts_open"
    )
    assert summary["stopped"] == [{"conversation_id": waits, "message": message}]
    assert (summary["landed"], summary["held"]) == (1, 0)
    assert pending(binary, waits) == [1]
    assert not (binary.home / "spool" / "held").exists()
    assert turns(server, waits) == []
    assert [t["messages"][0]["content"] for t in turns(server, lands)] == ["lands"]
    stub.next_run()
    assert land(binary)["landed"] == 1
    assert [t["messages"][0]["content"] for t in turns(server, waits)] == ["waits"]


def test_a_server_without_turn_records_takes_each_turn_as_one_released_append_turn(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-whole"
    stub.add(
        Rule(
            when=route_is("capabilities"),
            kind="rewrite",
            rewrite=without_records,
            times=99,
        )
    )
    queue(binary, hold_lock, stub.url, session, 'one é "quoted"')
    queue(binary, hold_lock, stub.url, session, "two", 2_000)
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (2, 0)
    assert not stub.records()
    appends = [e for e in stub.log if e.route == APPEND]
    stored = turns(server, session)
    assert len(appends) == len(stored) == 2
    actor = f"claude-code:{session}"
    for sent, turn in zip(appends, stored, strict=True):
        assert sent.raw == legacy_body(session, turn["messages"], actor)
    assert all("messages" in row for row in rows(binary, session))
    # A refused value holds the turn; the next lands.
    n = queue(binary, hold_lock, stub.url, session, "three")
    queue(binary, hold_lock, stub.url, session, "four")
    body = refusal("value_error", "message 0: refused")
    stub.add(Rule(when=route_is(APPEND), kind="answer", status=400, body=body))
    stub.next_run()
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (1, 1)
    assert held(binary, session, n) == {
        "reason": "refused",
        "status": 400,
        "code": "value_error",
        "message": "message 0: refused",
    }
    contents = [t["messages"][0]["content"] for t in turns(server, session)]
    assert contents == ['one é "quoted"', "two", "four"]


def test_a_404_on_write_records_sends_each_turn_whole(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-missing"
    stub.add(Rule(when=route_is(WRITE), kind="answer", status=404, body="", times=99))
    queue(binary, hold_lock, stub.url, session, "one")
    queue(binary, hold_lock, stub.url, session, "two")
    assert land(binary)["landed"] == 2
    assert [e.body["records"] for e in stub.records()] == [[], []]
    appends = [e for e in stub.log if e.route == APPEND]
    stored = turns(server, session)
    actor = f"claude-code:{session}"
    assert [e.raw for e in appends] == [
        legacy_body(session, turn["messages"], actor) for turn in stored
    ]


@pytest.mark.parametrize("gone", [True, False])
def test_a_configuration_refusal_reads_the_capability_again_once(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub], gone: bool
) -> None:
    server, stub = remote
    session = "rows-reread"
    n = queue(binary, hold_lock, stub.url, session, "reread")
    message = "this store does not keep turn records"
    body = refusal("agent_configuration_error", message)
    first = Rule(
        when=lambda e: e.route == WRITE and bool(e.body["records"]), kind="answer"
    )
    first.status, first.body = 400, body
    stub.add(first)
    if gone:
        later = Rule(
            when=lambda e: e.route == "capabilities" and e.nth > 1,
            kind="rewrite",
            rewrite=without_records,
        )
        stub.add(later)
    summary = land(binary)
    assert len([e for e in stub.log if e.route == "capabilities"]) == 2
    appends = [e for e in stub.log if e.route == APPEND]
    if gone:
        assert (summary["landed"], len(appends)) == (1, 1)
        assert "messages" in rows(binary, session)[0]
    else:
        # Ruling E15: the destination's fault keeps the turn, never holds it.
        told = f"[agent_configuration_error] {message}"
        assert summary["stopped"] == [{"conversation_id": session, "message": told}]
        assert (summary["held"], appends) == (0, [])
        assert pending(binary, session) == [n]


def test_a_later_turn_never_overtakes_one_still_queued_for_its_destination(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    first, other = "rows-order-a", "rows-order-b"
    n = queue(binary, hold_lock, stub.url, first, "one")
    queue(binary, hold_lock, stub.url, first, "two")
    queue(binary, hold_lock, stub.url, other, "elsewhere")
    draft = draft_id(first, opening(binary, first, n))
    down = Rule(
        when=lambda e: e.route == WRITE and e.body["draft"] == draft, kind="answer"
    )
    down.status, down.body = 503, "down"
    stub.add(down)
    summary = land(binary)
    told = "the destination answered 503: down"
    assert summary["stopped"] == [{"conversation_id": first, "message": told}]
    assert summary["landed"] == 0
    # Nothing after the kept turn is sent, of its conversation or its
    # destination's other conversations.
    assert [e.body["draft"] for e in stub.records()] == [draft]
    assert pending(binary, first) == [1, 2] and pending(binary, other) == [1]
    stub.next_run()
    assert land(binary)["landed"] == 3
    assert [t["messages"][0]["content"] for t in turns(server, first)] == ["one", "two"]


@pytest.mark.parametrize(
    ("status", "body", "headers", "told"),
    [
        (429, "", {"Retry-After": "1"}, "the destination answered 429"),
        (429, "", {}, "the destination answered 429"),
        (401, "", {}, "the destination answered 401"),
        (403, refusal("forbidden", "no writes"), {}, "[forbidden] no writes"),
        (413, refusal("storage_exceeded", "full"), {}, "[storage_exceeded] full"),
        (500, "", {}, "the destination answered 500"),
        (302, "", {"Location": "http://elsewhere/"}, "the destination answered 302"),
    ],
)
def test_an_answer_about_the_destination_keeps_the_turn_queued(
    binary: Binary,
    hold_lock: Hold,
    remote: tuple[Server, Stub],
    status: int,
    body: Any,
    headers: dict[str, str],
    told: str,
) -> None:
    """The destination rows wait or block in T1e1e; here they keep the turn."""
    server, stub = remote
    session = "rows-destination"
    queue(binary, hold_lock, stub.url, session, "kept")
    rule = Rule(when=route_is(WRITE), kind="answer", status=status, body=body)
    rule.headers = headers
    stub.add(rule)
    summary = land(binary)
    assert summary["stopped"] == [{"conversation_id": session, "message": told}]
    assert (summary["landed"], summary["held"]) == (0, 0)
    assert pending(binary, session) == [1]
    assert len(stub.records()) == 1
    stub.next_run()
    assert land(binary)["landed"] == 1
    assert len(turns(server, session)) == 1


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (409, "conflict_elsewhere", None),
        (400, FORMAT, "turn 3: a stored line is unreadable"),
    ],
    ids=["409", "format of the conversation"],
)
def test_another_refusal_with_an_envelope_holds_the_turn_with_its_message(
    binary: Binary,
    hold_lock: Hold,
    remote: tuple[Server, Stub],
    status: int,
    code: str,
    reason: str | None,
) -> None:
    server, stub = remote
    session = "rows-other-4xx"
    n = queue(binary, hold_lock, stub.url, session, "refused")
    queue(binary, hold_lock, stub.url, session, "accepted")
    draft = draft_id(session, opening(binary, session, n))
    message = f"refused here: {reason or code}"
    details = {"conversation_id": session, "reason": reason} if reason else {}
    body = {"error": {"code": code, "message": message, "details": details}}
    stub.add(Rule(when=part_of(draft), kind="answer", status=status, body=body))
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (1, 1)
    expected = {"reason": "refused", "status": status, "code": code}
    assert held(binary, session, n) == expected | {"message": message}
    assert [t["messages"][0]["content"] for t in turns(server, session)] == ["accepted"]


def test_a_format_refusal_of_the_turn_s_own_draft_gets_the_fresh_id(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-own-format"
    n = queue(binary, hold_lock, stub.url, session, "own draft")
    line = opening(binary, session, n)
    draft, fresh = draft_id(session, line), fresh_id(session, line)
    why = "a draft's state is unreadable"
    details = {"conversation_id": session, "reason": why}
    body = {"error": {"code": FORMAT, "message": why, "details": details}}
    stub.add(Rule(when=part_of(draft), kind="answer", status=400, body=body))
    assert land(binary)["landed"] == 1
    assert rows(binary, session)[0]["draft"] == fresh


def test_an_unreadable_answer_to_append_turn_holds_the_turn_and_never_resends_it(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-unreadable"
    stub.add(
        Rule(
            when=route_is("capabilities"),
            kind="rewrite",
            rewrite=without_records,
            times=99,
        )
    )
    n = queue(binary, hold_lock, stub.url, session, "maybe stored")
    queue(binary, hold_lock, stub.url, session, "next")
    stub.add(Rule(when=route_is(APPEND), kind="answer", status=200, body={"turn": 3}))
    summary = land(binary)
    assert (summary["landed"], summary["held"]) == (1, 1)
    assert held(binary, session, n) == {
        "reason": "unreadable",
        "message": "a 2xx was answered but could not be read; it may be stored",
    }
    assert (binary.home / "spool" / "held" / session / f"{n}.jsonl").is_file()
    assert [t["messages"][0]["content"] for t in turns(server, session)] == ["next"]
    before = len(stub.log)
    stub.next_run()
    assert land(binary)["landed"] == 0
    assert not [e for e in stub.log[before:] if e.route == APPEND]


def test_a_lost_append_turn_keeps_the_turn_and_sends_it_again(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "rows-lost-append"
    stub.add(
        Rule(
            when=route_is("capabilities"),
            kind="rewrite",
            rewrite=without_records,
            times=99,
        )
    )
    n = queue(binary, hold_lock, stub.url, session, "sent twice")
    stub.add(Rule(when=route_is(APPEND), kind="cut"))
    first = land(binary)
    assert first["landed"] == 0
    assert first["stopped"][0]["message"].startswith("no answer from the destination: ")
    assert pending(binary, session) == [n]
    stub.next_run()
    assert land(binary)["landed"] == 1
    appends = [e for e in stub.log if e.route == APPEND]
    assert len(appends) == 2 and appends[0].raw == appends[1].raw
    assert [t["messages"][0]["content"] for t in turns(server, session)] == [
        "sent twice"
    ]
