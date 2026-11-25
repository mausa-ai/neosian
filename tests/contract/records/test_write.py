"""`conversation/write_records` (DESIGN §40.54 items 3, 8.2, 9 and 14).

Literal requests and literal answers. The byte counts are computed here
from item 14's rule and stated beside the design's own numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.contract.records.example import (
    CHUNKS,
    DRAFT,
    EXAMPLE,
    MESSAGES,
    RESULT,
    SESSION,
)
from tests.contract.records.support import (
    bookkeeping,
    conflict,
    conversation_row,
    header_bytes,
    nothing,
    probe,
    prompts,
    record,
    records_bytes,
    refused,
    write,
)
from tests.contract.support import ADMIN, Binary, Server, serve

SHORT = [
    record(0, 0, "user", "prompt", "héllo"),
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
SHORT_BYTES = 6 + 16 + 11 + 10


def stamp(text: str) -> datetime:
    assert text.endswith("Z")
    return datetime.fromisoformat(text.removesuffix("Z") + "+00:00")


def test_the_design_example_lands_in_two_parts_with_its_literal_bytes(
    enabled: Binary,
) -> None:
    """Item 9's example: turn 42 of `cc-7f3a`, written by `client:default`."""
    assert len(RESULT.encode()) == 812_345 and len(CHUNKS) == 13
    assert [len(chunk) for chunk in CHUNKS] == [65_000] * 12 + [32_345]
    actor = "client:default/claude-code:7f3a"
    first, second = EXAMPLE[:9], EXAMPLE[9:]
    # Item 14, from the request alone; the design prints the same four numbers.
    assert records_bytes(SESSION, first) + bookkeeping(SESSION, DRAFT) == 458_588
    sealed_bytes = records_bytes(SESSION, second) + header_bytes(SESSION, DRAFT, actor)
    assert sealed_bytes == 359_889
    assert bookkeeping(SESSION, DRAFT) == 1_054
    token = {"NEOSIAN_SERVE_TOKEN": f"client:default={ADMIN}"}
    with serve(enabled, extra_env=token) as server:
        for number in range(1, 42):
            appended = server.post(
                "conversation/append_turn",
                conversation_id=SESSION,
                messages=[{"role": "user", "content": f"turn {number}"}],
            )
            assert appended["turn"]["turn"] == number
        opened = write(
            server,
            SESSION,
            DRAFT,
            first,
            actor="claude-code:7f3a",
            turn_bytes=812_400,
        )
        assert opened == {
            "draft": "d7c1e0b2a9f44e57",
            "state": "open",
            "next_seq": 9,
            "turn": None,
            "stored_bytes": 458_588,
            "freed_bytes": 0,
        }
        before = datetime.now(UTC) - timedelta(seconds=1)
        sealed = write(
            server, SESSION, DRAFT, second, actor="claude-code:7f3a", seal=True
        )
        after = datetime.now(UTC) + timedelta(seconds=1)
        created_at = sealed["turn"]["created_at"]
        assert before <= stamp(created_at) <= after
        assert sealed == {
            "draft": "d7c1e0b2a9f44e57",
            "state": "sealed",
            "next_seq": 16,
            "turn": {
                "turn": 42,
                "created_at": created_at,
                "actor": "client:default/claude-code:7f3a",
                "redacted": False,
                "records": 16,
                "bytes": 812_400,
            },
            "stored_bytes": 359_889,
            "freed_bytes": 1_054,
        }
        read = server.post("conversation/read_turns", conversation_id=SESSION, after=41)
        assert read["turns"] == [
            {
                "conversation_id": SESSION,
                "turn": 42,
                "messages": MESSAGES,
                "created_at": created_at,
                "actor": actor,
                "redacted": False,
            }
        ]
        number = server.post("conversation/last_turn_number", conversation_id=SESSION)
        assert number == {"turn": 42}


def test_a_probe_an_opening_repeats_a_seal_and_a_second_seal(records: Server) -> None:
    session, draft, actor = "s-1", "part_1-a", "client:contract"
    assert probe(records, session, draft) == nothing(draft)
    assert records.post("store/conversations") == {"conversations": []}
    opened = write(records, session, draft, SHORT[:2], turn_bytes=SHORT_BYTES)
    assert opened == {
        "draft": draft,
        "state": "open",
        "next_seq": 2,
        "turn": None,
        "stored_bytes": records_bytes(session, SHORT[:2]) + bookkeeping(session, draft),
        "freed_bytes": 0,
    }
    # 2c is 6 and o is 8: two records, then the bookkeeping.
    assert opened["stored_bytes"] == (6 + 6 + 256) + (16 + 7 + 4 + 6 + 256) + 1_038
    still = {**opened, "stored_bytes": 0}
    assert probe(records, session, draft) == still
    assert write(records, session, draft, SHORT[:2]) == still
    assert write(records, session, draft, SHORT[1:2], turn_bytes=SHORT_BYTES) == still
    # A repeat under other part boundaries stores only what is new.
    overlap = write(records, session, draft, SHORT[1:4])
    assert overlap == {
        **opened,
        "next_seq": 4,
        "stored_bytes": records_bytes(session, SHORT[2:4]),
    }
    assert write(records, session, draft, SHORT[:4]) == {**overlap, "stored_bytes": 0}
    before = datetime.now(UTC) - timedelta(seconds=1)
    sealed = write(records, session, draft, SHORT[3:], seal=True)
    created_at = sealed["turn"]["created_at"]
    assert before <= stamp(created_at) <= datetime.now(UTC) + timedelta(seconds=1)
    header = {
        "turn": 1,
        "created_at": created_at,
        "actor": actor,
        "redacted": False,
        "records": 5,
        "bytes": SHORT_BYTES,
    }
    created = header_bytes(session, draft, actor) + conversation_row(session)
    assert sealed == {
        "draft": draft,
        "state": "sealed",
        "next_seq": 5,
        "turn": header,
        "stored_bytes": records_bytes(session, SHORT[4:]) + created,
        "freed_bytes": bookkeeping(session, draft),
    }
    assert created == (15 + 8 + 4 * 3 + 512) + (3 + 256)
    again = {**sealed, "stored_bytes": 0, "freed_bytes": 0}
    assert probe(records, session, draft) == again
    assert write(records, session, draft, [], seal=True) == again
    assert write(records, session, draft, SHORT, seal=True) == again
    assert write(records, session, draft, SHORT[2:4], turn_bytes=SHORT_BYTES) == again
    read = records.post("conversation/read_turns", conversation_id=session)
    assert read["turns"] == [
        {
            "conversation_id": session,
            "turn": 1,
            "messages": [
                {
                    "role": "user",
                    "content": "héllo",
                    "reasoning": None,
                    "tool_calls": [],
                    "tool_call_id": None,
                },
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning": None,
                    "tool_calls": [
                        {
                            "id": "toolu_1",
                            "name": "Bash",
                            "arguments": {"command": "ls"},
                        }
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
            ],
            "created_at": created_at,
            "actor": actor,
            "redacted": False,
        }
    ]
    # A second draft of the same conversation takes the next number.
    second = write(records, session, "next", prompts(1), seal=True)
    assert second["turn"]["turn"] == 2
    assert second["stored_bytes"] == (
        records_bytes(session, prompts(1))
        + bookkeeping(session, "next")
        + header_bytes(session, "next", actor)
    )
    assert second["freed_bytes"] == bookkeeping(session, "next")


def test_an_open_draft_is_visible_to_no_reader_until_its_seal(records: Server) -> None:
    session = "s-hidden"
    write(records, session, "d", [record(0, 0, "user", "prompt", "needle")])
    assert records.post("conversation/read_turns", conversation_id=session) == {
        "turns": [],
        "next_after": None,
    }
    number = records.post("conversation/last_turn_number", conversation_id=session)
    assert number == {"turn": 0}
    assert records.post("store/conversations") == {"conversations": []}
    search = {"query": "needle", "conversations": [session]}
    assert records.post("conversation/search_turns", **search) == {"turns": []}
    sealed = write(records, session, "d", [], seal=True)
    assert sealed["turn"]["turn"] == 1
    assert records.post("store/conversations") == {"conversations": [session]}
    number = records.post("conversation/last_turn_number", conversation_id=session)
    assert number == {"turn": 1}
    found = records.post("conversation/search_turns", **search)
    assert [turn["turn"] for turn in found["turns"]] == [1]


def test_a_discard_drops_the_draft_and_reports_its_bytes(records: Server) -> None:
    session, draft = "s-drop", "d"
    assert write(records, session, draft, [], discard=True) == nothing(draft)
    opened = write(records, session, draft, SHORT[:3])
    dropped = write(records, session, draft, [], discard=True)
    assert dropped == {
        "draft": draft,
        "state": "discarded",
        "next_seq": 3,
        "turn": None,
        "stored_bytes": 0,
        "freed_bytes": opened["stored_bytes"],
    }
    assert opened["stored_bytes"] == (
        records_bytes(session, SHORT[:3]) + bookkeeping(session, draft)
    )
    assert probe(records, session, draft) == nothing(draft)
    base = {"conversation_id": session, "draft": draft}
    refused(records, {**base, "records": SHORT[:1], "discard": True})
    refused(records, {**base, "records": [], "discard": True, "seal": True})
    assert probe(records, session, draft) == nothing(draft)
    # The name is free again.
    assert write(records, session, draft, SHORT, seal=True)["turn"]["turn"] == 1


def test_an_actor_is_fixed_by_the_opening_and_may_be_omitted_later(
    records: Server,
) -> None:
    session = "s-actor"
    body = {"conversation_id": session, "draft": "named"}
    write(records, session, "named", prompts(1), actor="me")
    write(records, session, "named", prompts(1, 1))
    write(records, session, "named", prompts(1, 2), actor=None)
    write(records, session, "named", prompts(1, 3), actor="me")
    other = {**body, "records": prompts(1, 4), "actor": "you"}
    assert conflict(records, session, "draft_actor", other) == {"draft": "named"}
    assert probe(records, session, "named")["next_seq"] == 4
    sealed = write(records, session, "named", [], seal=True)
    assert sealed["turn"]["actor"] == "client:contract/me"
    assert conflict(records, session, "draft_actor", {**other, "records": []}) == {
        "draft": "named"
    }
    # An opening that names no actor is stamped as `append_turn` stamps it.
    write(records, session, "bare", prompts(1), actor=None)
    named = {"conversation_id": session, "draft": "bare", "actor": "me"}
    late = {**named, "records": prompts(1, 1)}
    assert conflict(records, session, "draft_actor", late) == {"draft": "bare"}
    bare = write(records, session, "bare", prompts(1, 1), seal=True)
    assert bare["turn"]["actor"] == "client:contract"
    assert bare["stored_bytes"] == (
        records_bytes(session, prompts(1))
        + header_bytes(session, "bare", "client:contract")
    )
    appended = records.post(
        "conversation/append_turn",
        conversation_id=session,
        messages=[{"role": "user", "content": "whole"}],
    )
    assert appended["turn"]["actor"] == bare["turn"]["actor"]
    read = records.post("conversation/read_turns", conversation_id=session)
    actors = [turn["actor"] for turn in read["turns"]]
    assert actors == ["client:contract/me", "client:contract", "client:contract"]


def test_gap_mismatch_and_sealed_conflicts_carry_their_details(
    records: Server,
) -> None:
    session, draft = "s-conflict", "d"
    body = {"conversation_id": session, "draft": draft}
    write(records, session, draft, SHORT[:2])
    gap = {**body, "records": SHORT[3:]}
    assert conflict(records, session, "draft_gap", gap) == {"next_seq": 2}
    changed = [SHORT[0], {**SHORT[1], "name": "Shell"}, SHORT[2]]
    mismatch = {**body, "records": changed}
    assert conflict(records, session, "draft_mismatch", mismatch) == {
        "next_seq": 2,
        "seq": 1,
    }
    assert probe(records, session, draft)["next_seq"] == 2, "nothing was stored"
    sealed = write(records, session, draft, SHORT[2:], seal=True)
    header = sealed["turn"]
    more = {**body, "records": [record(5, 4, "user", "prompt", "more")]}
    assert conflict(records, session, "draft_sealed", more) == {"turn": header}
    drop = {**body, "records": [], "discard": True}
    assert conflict(records, session, "draft_sealed", drop) == {"turn": header}
    assert conflict(records, session, "draft_mismatch", mismatch) == {
        "next_seq": 5,
        "seq": 1,
    }
    assert probe(records, session, draft)["turn"] == header


def test_a_stated_total_is_pinned(records: Server) -> None:
    session = "s-size"
    body = {"conversation_id": session, "draft": "d"}
    # A record that would take the draft past the stated total.
    past = {**body, "records": SHORT[:2], "turn_bytes": 21}
    assert conflict(records, session, "draft_size", past) == {
        "turn_bytes": 21,
        "bytes": 22,
    }
    assert probe(records, session, "d") == nothing("d")
    write(records, session, "d", SHORT[:2], turn_bytes=SHORT_BYTES)
    # A later request naming another value.
    other = {**body, "records": SHORT[2:3], "turn_bytes": SHORT_BYTES + 1}
    assert conflict(records, session, "draft_size", other) == {
        "turn_bytes": SHORT_BYTES + 1,
        "bytes": 22,
    }
    # A seal whose bytes differ from it.
    short = {**body, "records": SHORT[2:4], "seal": True}
    assert conflict(records, session, "draft_size", short) == {
        "turn_bytes": SHORT_BYTES,
        "bytes": 33,
    }
    long = [*SHORT[2:4], record(4, 3, "assistant", "reply", "Two files!!")]
    assert conflict(records, session, "draft_size", {**body, "records": long}) == {
        "turn_bytes": SHORT_BYTES,
        "bytes": SHORT_BYTES + 1,
    }
    assert probe(records, session, "d")["next_seq"] == 2, "the draft stays open"
    sealed = write(records, session, "d", SHORT[2:], seal=True)
    assert sealed["turn"]["bytes"] == SHORT_BYTES
    # A later request naming one on a draft opened without it.
    write(records, session, "free", SHORT[:2])
    late = {**body, "draft": "free", "records": [], "turn_bytes": 22}
    assert conflict(records, session, "draft_size", late) == {
        "turn_bytes": 22,
        "bytes": 22,
    }
    assert write(records, session, "free", SHORT[2:], seal=True)["state"] == "sealed"


def test_a_seventeenth_open_draft_is_refused(records: Server) -> None:
    session = "s-many"
    for index in range(16):
        opened = write(records, session, f"d{index}", prompts(1))
        assert opened["state"] == "open"
    body = {"conversation_id": session, "draft": "d16", "records": prompts(1)}
    assert conflict(records, session, "drafts_open", body) == {"limit": 16}
    assert probe(records, session, "d16") == nothing("d16")
    # An open draft still continues, and another conversation is not counted.
    assert write(records, session, "d0", prompts(1, 1))["next_seq"] == 2
    assert write(records, "s-other", "d16", prompts(1))["state"] == "open"
    write(records, session, "d1", [], discard=True)
    assert write(records, session, "d16", prompts(1))["state"] == "open"
