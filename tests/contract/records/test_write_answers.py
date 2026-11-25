"""`write_records` answers as bytes (DESIGN §40.54 items 8.2, 9 and 14, with
rulings R1 to R3 of the T1d plan): the key order inside each conflict's
details, the answer's size bound, the ceiling a declared length is refused
with, and a discard checked like any later request."""

from __future__ import annotations

from typing import Any

from tests.contract.records.support import (
    ROUTE,
    compact,
    conflict,
    declared,
    probe,
    prompts,
    raw,
    record,
    write,
)
from tests.contract.support import ADMIN, Server

SESSION = "s-raw"


def refusal(reason: str, details: dict[str, Any]) -> tuple[int, bytes]:
    """A conflict's envelope byte for byte, `details` in item 8.2's order."""
    error = {
        "code": "agent_conversation_conflict",
        "message": f"Conversation conflict on '{SESSION}': {reason}",
        "details": {"conversation_id": SESSION, "reason": reason, **details},
    }
    return 400, compact({"error": error})


def test_conflict_details_keep_their_key_order_in_the_raw_answer(
    records: Server,
) -> None:
    body: dict[str, Any] = {"conversation_id": SESSION, "draft": "d"}
    one = prompts(4)
    opened = raw(records, {**body, "records": one[:1], "turn_bytes": 3, "actor": "me"})
    assert opened == (
        200,
        b'{"draft":"d","state":"open","next_seq":1,"turn":null,'
        b'"stored_bytes":1302,"freed_bytes":0}',
    )
    # Item 14 with c = 5 and o = 1: 267 a record, 1035 the bookkeeping, 551
    # the header under an 18-byte actor, 261 the conversation row.
    assert (267 + 1_035, 2 * 267 + 551 + 261) == (1_302, 1_346)
    gap = raw(records, {**body, "records": one[2:3]})
    assert gap == refusal("draft_gap", {"next_seq": 1})
    changed = raw(records, {**body, "records": [{**one[0], "text": "b"}]})
    assert changed == refusal("draft_mismatch", {"next_seq": 1, "seq": 0})
    other = raw(records, {**body, "records": [], "actor": "you"})
    assert other == refusal("draft_actor", {"draft": "d"})
    past = raw(records, {**body, "records": one[1:]})
    assert past == refusal("draft_size", {"turn_bytes": 3, "bytes": 4})
    status, sealed = raw(records, {**body, "records": one[1:3], "seal": True})
    header = probe(records, SESSION, "d")["turn"]
    assert (status, sealed) == (
        200,
        b'{"draft":"d","state":"sealed","next_seq":3,"turn":'
        + compact(header)
        + b',"stored_bytes":1346,"freed_bytes":1035}',
    )
    assert compact(header).startswith(b'{"turn":1,"created_at":"')
    assert compact(header).endswith(
        b'","actor":"client:contract/me","redacted":false,"records":3,"bytes":3}'
    )
    more = raw(records, {**body, "records": one[3:]})
    assert more == refusal("draft_sealed", {"turn": header})
    for index in range(16):
        write(records, SESSION, f"open{index}", one[:1])
    full = raw(records, {**body, "draft": "open16", "records": one[:1]})
    assert full == refusal("drafts_open", {"limit": 16})


def test_an_answer_is_within_2048_bytes_plus_six_per_actor_byte(
    records: Server,
) -> None:
    """Ruling R1: no length bounds an actor, and a control character
    answers as six bytes, so the bound carries `6 * u(actor)`."""
    session, draft = "x" * 128, "d" * 64
    body: dict[str, Any] = {"conversation_id": session, "draft": draft}
    status, plain = raw(records, {**body, "records": prompts(2), "seal": True})
    assert status == 200 and len(plain) <= 2_048
    named = "\x01" * 300 + "\x7f" + "é" * 250 + "a" * 999
    stamped = "client:contract/" + named
    assert len(stamped.encode()) == 16 + 300 + 1 + 500 + 999
    bound = 2_048 + 6 * len(stamped.encode())
    long = {**body, "draft": "long", "actor": named}
    status, opened = raw(records, {**long, "records": prompts(1)})
    assert status == 200 and len(opened) <= 2_048
    status, sealed = raw(records, {**long, "records": [], "seal": True})
    assert status == 200 and 2_048 < len(sealed) <= bound
    assert sealed.count(b"\\u0001") == 300
    header = probe(records, session, "long")["turn"]
    assert header["actor"] == stamped
    status, after = raw(records, {**long, "records": prompts(1, 1)})
    assert status == 400 and b'"reason":"draft_sealed"' in after
    assert 2_048 < len(after) <= bound
    read = records.post("conversation/read_turns", conversation_id=session)
    assert [turn["actor"] for turn in read["turns"]] == ["client:contract", stamped]


def test_a_declared_length_is_refused_with_its_routes_ceiling(
    records: Server,
) -> None:
    """Ruling R2: a body declared past 64 MiB is refused before it is read,
    naming the ceiling of the route it was sent to."""

    def error(message: str) -> bytes:
        return compact(
            {"error": {"code": "value_error", "message": message, "details": {}}}
        )

    path, large = "/v1/" + ROUTE, "HTTP/1.1 413 Payload Too Large"
    part = error("request body exceeds 4194304 bytes")
    assert declared(records, path, 67_108_865, token=ADMIN) == (large, part)
    assert declared(records, path, 1 << 40, token=ADMIN) == (large, part)
    over = records.request(path, raw=b" " * 4_194_305)
    assert (over.status, compact(over.body)) == (413, part)
    whole = error("request body exceeds 67108864 bytes")
    for legacy in ["conversation/append_turn", "memory/write", "store/scopes"]:
        answer = declared(records, "/v1/" + legacy, 67_108_865, token=ADMIN)
        assert answer == (large, whole)
    for route in [path, "/v1/conversation/append_turn"]:
        unnamed = declared(records, route, 67_108_865)
        assert unnamed == ("HTTP/1.1 401 Unauthorized", b"unauthorized")


def test_a_discard_is_checked_like_any_later_request(records: Server) -> None:
    """Ruling R3: a discard naming another actor or another total is
    refused and discards nothing."""
    session = "s-discard"
    body: dict[str, Any] = {
        "conversation_id": session,
        "draft": "d",
        "records": [],
        "discard": True,
    }
    one = [record(0, 0, "user", "prompt", "hello")]
    opened = write(records, session, "d", one, actor="me", turn_bytes=9)
    other = {**body, "actor": "you"}
    assert conflict(records, session, "draft_actor", other) == {"draft": "d"}
    another = {**body, "turn_bytes": 10}
    assert conflict(records, session, "draft_size", another) == {
        "turn_bytes": 10,
        "bytes": 5,
    }
    assert probe(records, session, "d") == {**opened, "stored_bytes": 0}
    dropped = write(records, session, "d", [], discard=True, actor="me", turn_bytes=9)
    assert (dropped["state"], dropped["freed_bytes"]) == (
        "discarded",
        opened["stored_bytes"],
    )
    write(records, session, "free", one)
    named = {**body, "draft": "free", "turn_bytes": 5}
    assert conflict(records, session, "draft_size", named) == {
        "turn_bytes": 5,
        "bytes": 5,
    }
    assert probe(records, session, "free")["state"] == "open"
    assert write(records, session, "free", [], discard=True)["state"] == "discarded"
