"""Optional turn records v1: `conversation/write_records`.

A turn is written in parts into a draft the client names and appears whole
at the seal. These cases check the probe, the opening, repeats under other
part boundaries, the seal and its repeats, the discard, every `draft_*`
conflict with its details, strict decoding, both `413` refusals that need
no server setting, and the logical bytes of each answer, computed here from
the published size rule and the request alone. Fixtures are ASCII and stay
inside the value range every store accepts. Absent support is an explicit
skip under the full and base profiles.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.contract.records.support import (
    ANSWER_KEYS,
    BODY_CEILING,
    BOUND_PREFIX,
    HEADER_KEYS,
    PART_PREFIX,
    ROUTE,
    bookkeeping,
    conversation_row,
    declared,
    header_bytes,
    nothing,
    probe,
    prompts,
    record,
    records_bytes,
    send,
    write,
)
from tests.contract.storage.support import (
    Storage,
    conflicted,
    refused,
    require_records,
    sent as answer_bytes,
    too_large,
)

TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z")
SHORT = [
    record(0, 0, "user", "prompt", "list the files"),
    record(
        1,
        1,
        "assistant",
        "tool_call",
        '{"command":"ls"}',
        content="none",
        call=0,
        call_id="toolu_1",
        name="Bash",
    ),
    record(2, 2, "tool", "tool_result", "a.txt\n", call_id="toolu_1", size=11),
    record(
        3,
        2,
        "tool",
        "tool_result",
        "b.txt",
        content=None,
        call_id="toolu_1",
        chunk=1,
        size=11,
    ),
    record(4, 3, "assistant", "reply", "Two files."),
]
SHORT_BYTES = 14 + 16 + 11 + 10
SHORT_MESSAGES: list[dict[str, Any]] = [
    {
        "role": "user",
        "content": "list the files",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    },
    {
        "role": "assistant",
        "content": None,
        "reasoning": None,
        "tool_calls": [
            {"id": "toolu_1", "name": "Bash", "arguments": {"command": "ls"}}
        ],
        "tool_call_id": None,
    },
    {
        "role": "tool",
        "content": "a.txt\nb.txt",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": "toolu_1",
    },
    {
        "role": "assistant",
        "content": "Two files.",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    },
]


@pytest.fixture(autouse=True)
def records_capability(storage: Storage) -> None:
    require_records(storage)


def body(storage: Storage, draft: str, items: list[Any], **more: Any) -> dict[str, Any]:
    return {
        "conversation_id": storage.conversation,
        "draft": draft,
        "records": items,
        **more,
    }


def test_a_draft_is_probed_opened_repeated_and_sealed(storage: Storage) -> None:
    server, session, draft = storage.server, storage.conversation, "part_1-a"
    assert probe(server, session, draft) == nothing(draft)
    opened = write(server, session, draft, SHORT[:2], turn_bytes=SHORT_BYTES)
    assert opened == {
        "draft": draft,
        "state": "open",
        "next_seq": 2,
        "turn": None,
        "stored_bytes": records_bytes(session, SHORT[:2]) + bookkeeping(session, draft),
        "freed_bytes": 0,
    }
    still = {**opened, "stored_bytes": 0}
    assert probe(server, session, draft) == still
    # The second worker sees the same draft and takes a pure repeat.
    assert probe(storage.peer, session, draft) == still
    assert write(storage.peer, session, draft, SHORT[:2]) == still
    assert write(server, session, draft, SHORT[1:2], turn_bytes=SHORT_BYTES) == still
    # A repeat under other part boundaries stores only what is new.
    overlap = write(server, session, draft, SHORT[1:4])
    assert overlap == {
        **opened,
        "next_seq": 4,
        "stored_bytes": records_bytes(session, SHORT[2:4]),
    }
    assert write(server, session, draft, SHORT[:4]) == {**overlap, "stored_bytes": 0}
    sealed = write(server, session, draft, SHORT[3:], seal=True)
    created_at = sealed["turn"]["created_at"]
    assert TIME.fullmatch(created_at)
    header = {
        "turn": 1,
        "created_at": created_at,
        "actor": storage.client,
        "redacted": False,
        "records": 5,
        "bytes": SHORT_BYTES,
    }
    created = header_bytes(session, draft, storage.client) + conversation_row(session)
    assert sealed == {
        "draft": draft,
        "state": "sealed",
        "next_seq": 5,
        "turn": header,
        "stored_bytes": records_bytes(session, SHORT[4:]) + created,
        "freed_bytes": bookkeeping(session, draft),
    }
    # Sealing again, with the same records or none, answers the same header.
    again = {**sealed, "stored_bytes": 0, "freed_bytes": 0}
    assert probe(server, session, draft) == again
    assert write(server, session, draft, [], seal=True) == again
    assert write(storage.peer, session, draft, SHORT, seal=True) == again
    # The turn appears whole, as the messages its records make.
    assert storage.turns("read_turns")["turns"] == [
        {
            "conversation_id": session,
            "turn": 1,
            "messages": SHORT_MESSAGES,
            "created_at": created_at,
            "actor": storage.client,
            "redacted": False,
        }
    ]
    assert storage.turns("last_turn_number") == {"turn": 1}
    # A second turn numbers on, and creates no conversation row again.
    second = write(server, session, "part_2", prompts(1), seal=True, actor="me")
    assert second["turn"]["turn"] == 2
    assert second["turn"]["actor"] == storage.client + "/me"
    assert second["stored_bytes"] == (
        records_bytes(session, prompts(1))
        + bookkeeping(session, "part_2")
        + header_bytes(session, "part_2", storage.client + "/me")
    )
    assert second["freed_bytes"] == bookkeeping(session, "part_2")


def test_an_open_draft_is_invisible_and_a_discard_frees_it(storage: Storage) -> None:
    server, session, draft = storage.server, storage.conversation, "hidden"
    items = [record(0, 0, "user", "prompt", "unsealedneedle " + storage.namespace)]
    opened = write(server, session, draft, items)
    assert opened["state"] == "open"
    assert storage.turns("read_turns")["turns"] == []
    assert storage.turns("last_turn_number") == {"turn": 0}
    assert storage.search("unsealedneedle") == []
    page = server.post("conversation/read_records", conversation_id=session)
    assert page == {"turns": [], "records": [], "continuation": None}
    found = server.post(
        "conversation/search_records", query="unsealedneedle", conversations=[session]
    )
    assert found == {"hits": []}
    if storage.portable:
        assert session not in server.post("store/conversations")["conversations"]
    # A discard beside records or a seal is refused and discards nothing.
    beside: list[dict[str, Any]] = [{"records": items}, {"seal": True}]
    for wrong in beside:
        reply = send(server, {**body(storage, draft, []), "discard": True, **wrong})
        refused(reply, "value_error")
    discarded = write(server, session, draft, [], discard=True)
    assert discarded == {
        "draft": draft,
        "state": "discarded",
        "next_seq": 1,
        "turn": None,
        "stored_bytes": 0,
        "freed_bytes": opened["stored_bytes"],
    }
    assert opened["stored_bytes"] == records_bytes(session, items) + bookkeeping(
        session, draft
    )
    assert probe(server, session, draft) == nothing(draft)
    assert storage.turns("last_turn_number") == {"turn": 0}


def test_each_draft_conflict_names_its_reason_and_details(storage: Storage) -> None:
    server, session, draft = storage.server, storage.conversation, "conflicts"
    opened = write(server, session, draft, prompts(2), actor="one", turn_bytes=4)

    def conflict(reason: str, items: list[Any], **more: Any) -> dict[str, Any]:
        reply = send(server, body(storage, draft, items, **more))
        return conflicted(reply, session, reason)

    assert conflict("draft_gap", prompts(1, start=3)) == {"next_seq": 2}
    changed = [prompts(1)[0], record(1, 1, "user", "prompt", "b")]
    assert conflict("draft_mismatch", changed) == {"next_seq": 2, "seq": 1}
    assert conflict("draft_actor", [], actor="two") == {"draft": draft}
    assert conflict("draft_actor", [], actor="two", discard=True) == {"draft": draft}
    # The pinned total: three more bytes would take the draft past four.
    assert conflict("draft_size", prompts(3, start=2)) == {"turn_bytes": 4, "bytes": 5}
    # Another total than the one pinned: the request's value, the bytes held.
    assert conflict("draft_size", [], turn_bytes=5) == {"turn_bytes": 5, "bytes": 2}
    # A seal whose bytes differ from the pinned total.
    assert conflict("draft_size", [], seal=True) == {"turn_bytes": 4, "bytes": 2}
    # Nothing of a refused request was stored, and the draft stays open.
    assert probe(server, session, draft) == {**opened, "stored_bytes": 0}
    sealed = write(server, session, draft, prompts(2, start=2), seal=True)
    assert sealed["turn"]["bytes"] == 4
    header = sealed["turn"]
    assert conflict("draft_sealed", prompts(1, start=4)) == {"turn": header}
    assert conflict("draft_sealed", [], discard=True) == {"turn": header}
    # A total named on a draft opened without one.
    other = "untotalled"
    write(server, session, other, prompts(1))
    reply = send(server, body(storage, other, [], turn_bytes=1))
    assert conflicted(reply, session, "draft_size") == {"turn_bytes": 1, "bytes": 1}
    assert write(server, session, other, [], discard=True)["state"] == "discarded"


def test_a_conversation_holds_sixteen_open_drafts(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    for number in range(16):
        assert write(server, session, f"d{number}", prompts(1))["state"] == "open"
    reply = send(server, body(storage, "d16", prompts(1)))
    assert conflicted(reply, session, "drafts_open") == {"limit": 16}
    assert probe(server, session, "d16") == nothing("d16")
    # An open draft still takes its parts, and a freed place is taken again.
    assert write(server, session, "d3", prompts(1, start=1))["next_seq"] == 2
    assert write(server, session, "d0", [], discard=True)["state"] == "discarded"
    assert write(server, session, "d16", prompts(1))["state"] == "open"
    for number in range(1, 17):
        freed = write(server, session, f"d{number}", [], discard=True)
        assert freed["state"] == "discarded"
    assert storage.turns("last_turn_number") == {"turn": 0}


@pytest.mark.parametrize(
    "wrong",
    [
        {"note": 1},
        {"records": None},
        {"records": {}},
        {"draft": "Upper"},
        {"draft": ""},
        {"draft": "x" * 65},
        {"draft": 7},
        {"seal": "true"},
        {"seal": None},
        {"discard": 1},
        {"turn_bytes": -1},
        {"turn_bytes": "4"},
        {"turn_bytes": None},
        {"actor": 7},
        {"records": [{**prompts(1)[0], "weight": 1}]},
        {"records": [{**prompts(1)[0], "seq": "0"}]},
        {"records": [{**prompts(1)[0], "kind": "thought"}]},
        {"records": [{**prompts(1)[0], "role": "robot"}]},
        {"records": [{**prompts(1)[0], "kind": "reply"}]},
        {"records": [{**prompts(1)[0], "text": None}]},
        {"records": [{**prompts(1)[0], "size": 0}]},
        {"records": [{**prompts(1)[0], "chunk": 1}]},
        {"records": [{**prompts(1)[0], "content": None}]},
        {"records": [{**prompts(1)[0], "message": 1}]},
        {"records": [prompts(1)[0], prompts(1, start=2)[0]]},
        {"records": [record(0, 0, "user", "prompt", "a" * 65_535)]},
        {"records": prompts(2_049)},
    ],
)
def test_a_body_is_decoded_strictly_and_a_refusal_stores_nothing(
    storage: Storage, wrong: dict[str, Any]
) -> None:
    server, draft = storage.server, "strict"
    reply = send(server, {**body(storage, draft, prompts(1)), **wrong})
    refused(reply, "value_error")
    assert probe(server, storage.conversation, draft) == nothing(draft)


def test_the_bounds_of_a_request_are_inclusive(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    # A text of 65,534 bytes encodes to 65,536 with its quotes: the bound.
    longest = [record(0, 0, "user", "prompt", "a" * 65_534)]
    assert write(server, session, "x" * 64, longest)["next_seq"] == 1
    assert write(server, session, "x" * 64, [], discard=True)["state"] == "discarded"
    full = write(server, session, "full", prompts(2_048))
    assert full["next_seq"] == 2_048
    assert full["stored_bytes"] == records_bytes(session, prompts(2_048)) + bookkeeping(
        session, "full"
    )
    assert write(server, session, "full", [], discard=True)["freed_bytes"] == (
        full["stored_bytes"]
    )
    # A missing required field is refused.
    for missing in ["conversation_id", "draft", "records"]:
        partial = body(storage, "partial", prompts(1))
        del partial[missing]
        refused(send(server, partial), "value_error")
    assert probe(server, session, "partial") == nothing("partial")


def test_a_body_over_4_mib_is_413_before_it_is_parsed(storage: Storage) -> None:
    """The ceiling is checked while the body is read: one byte past it is
    `413` though the bytes are not JSON, and the same bytes at the ceiling
    are refused as what they are."""
    path = "/v1/" + ROUTE
    over = storage.server.request(path, raw=b"x" * (BODY_CEILING + 1))
    assert too_large(over, PART_PREFIX) == f"request body exceeds {BODY_CEILING} bytes"
    refused(storage.server.request(path, raw=b"x" * BODY_CEILING), "value_error")
    # Authentication still comes first, before any of the body is read.
    # On the local fixtures only: a plain socket declares the length and
    # sends none of it, and the status line is the reference server's.
    if storage.local:
        denied = declared(storage.server, path, BODY_CEILING + 1)
        assert denied == ("HTTP/1.1 401 Unauthorized", b"unauthorized")


def test_a_stated_total_no_store_keeps_is_413_at_the_opening(
    storage: Storage,
) -> None:
    """`turn_bytes` past the store's bound is refused before anything is
    stored. 2**62 is past every bound a store can set and inside the number
    range every store reads."""
    server, draft = storage.server, "too-long"
    reply = send(server, body(storage, draft, prompts(1), turn_bytes=1 << 62))
    said = too_large(reply, BOUND_PREFIX)
    assert not said.startswith(PART_PREFIX)
    assert probe(server, storage.conversation, draft) == nothing(draft)


def test_the_answer_writes_its_keys_in_order_within_its_bound(
    storage: Storage,
) -> None:
    server, session, draft = storage.server, storage.conversation, "ordered"
    status, sent = answer_bytes(
        server, ROUTE, body(storage, draft, prompts(1), seal=True)
    )
    assert status == 200
    answer = json.loads(sent)
    assert list(answer) == ANSWER_KEYS
    assert list(answer["turn"]) == HEADER_KEYS
    assert answer["stored_bytes"] == (
        records_bytes(session, prompts(1))
        + bookkeeping(session, draft)
        + header_bytes(session, draft, storage.client)
        + conversation_row(session)
    )
    assert answer["freed_bytes"] == bookkeeping(session, draft)
    # The bound is on the bytes as sent, and grows only with the actor.
    assert len(sent) <= 2_048 + 6 * len(storage.client.encode())
    again = write(server, session, draft, prompts(1), seal=True)
    assert again == {**answer, "stored_bytes": 0, "freed_bytes": 0}
