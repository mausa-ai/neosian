"""What a restore in parts shows and when (DESIGN §40.54 items 12 and 13):
nothing of a staged restore is visible to any read, listing or search until
its commit; a stale one is swept by `store/sweep_records`; a probe is not a
touch; and a committed restore reads as the conversation it was taken from.
"""

from __future__ import annotations

from typing import Any

from tests.contract.records.example import EXAMPLE, MESSAGES
from tests.contract.records.reading import read
from tests.contract.records.restoring import (
    ACTOR,
    DIGEST,
    END,
    EPOCH,
    ERASED,
    FIRST,
    HEADERS,
    ID,
    SECOND,
    SESSION,
    TURNS,
    answer,
    conflict,
    content_bytes,
    part,
    position,
    projection,
    restore_bookkeeping,
    status,
    unit_digest,
)
from tests.contract.records.searching import search, sweep
from tests.contract.records.support import write
from tests.contract.records.test_restore_conflicts import aged
from tests.contract.support import Binary, Server, serve

WORD = "x" * 32
NOTHING: dict[str, Any] = {"conversations": [], "next_cursor": None, "write": False}


def seen(server: Server, conversation: str) -> dict[str, Any]:
    """Everything the wire answers about one conversation and about the
    store's conversations, by every read, listing and search there is."""
    post = server.post
    named = [conversation]
    return {
        "read_turns": post("conversation/read_turns", conversation_id=conversation),
        "read_records": read(server, conversation, kinds=_ALL),
        "headers": read(server, conversation, kinds=[]),
        "last": post("conversation/last_turn_number", conversation_id=conversation),
        "projections": post(
            "conversation/read_projections", conversation_id=conversation
        ),
        "trail": post("conversation/turn_redactions", conversations=named),
        "every trail": post("conversation/turn_redactions"),
        "search_turns": post("conversation/search_turns", query=WORD),
        "search_turns named": post(
            "conversation/search_turns", query=WORD, conversations=named
        ),
        "search_turns ranked": post(
            "conversation/search_turns", query="cache", order="relevance"
        ),
        "search_records": search(server, WORD, conversations=named),
        "search_records ranked": search(
            server, "cache " + WORD, conversations=named, order="relevance"
        ),
        "every search_records": search(server, WORD),
        "listing": post("store/conversations"),
    }


ABSENT: dict[str, Any] = {
    "read_turns": {"turns": [], "next_after": None},
    "read_records": {"turns": [], "records": [], "continuation": None},
    "headers": {"turns": [], "records": [], "continuation": None},
    "last": {"turn": 0},
    "projections": {"entries": [], "next_after": None},
    "trail": {"redactions": []},
    "every trail": {"redactions": []},
    "search_turns": {"turns": []},
    "search_turns named": {"turns": []},
    "search_turns ranked": {"turns": [], "order": "relevance", "scores": []},
    "search_records": {"hits": []},
    "search_records ranked": {"hits": [], "order": "relevance"},
    "every search_records": {"hits": []},
    "listing": {"conversations": []},
}


def test_a_half_restored_conversation_is_absent_from_every_read_and_listing(
    enabled: Binary,
) -> None:
    """Item 12: until commit nothing of it is visible to any read, listing
    or search and `last_turn_number` answers 0, with every item of the
    stream already staged, across a restart, and after a discard."""
    with serve(enabled) as server:
        assert seen(server, SESSION) == ABSENT
        part(server, SESSION, ID, **FIRST)
        assert seen(server, SESSION) == ABSENT
        staged = part(server, SESSION, ID, **SECOND)
        assert staged["position"] == END
        assert seen(server, SESSION) == ABSENT
    with serve(enabled) as server:
        assert seen(server, SESSION) == ABSENT
        assert status(server, SESSION, ID) == answer(ID, "open", END)
        gone = part(server, SESSION, ID, discard=True)
        assert gone["freed_bytes"] == 4_002 + content_bytes(SESSION, ID, SECOND)
        assert seen(server, SESSION) == ABSENT
        # Staged again and committed, the same questions answer the whole.
        part(server, SESSION, ID, **FIRST)
        assert seen(server, SESSION) == ABSENT
        done = part(server, SESSION, ID, **SECOND, commit=True)
        assert done["digest"] == DIGEST
        after = seen(server, SESSION)
        assert after["read_turns"] == {"turns": TURNS, "next_after": None}
        assert after["headers"]["turns"] == HEADERS
        assert len(after["read_records"]["records"]) == 4
        assert after["last"] == {"turn": 3}
        assert after["projections"]["entries"] == [EPOCH]
        assert after["trail"] == after["every trail"] == {"redactions": [ERASED]}
        assert after["search_turns"] == {"turns": [TURNS[0]]}
        assert after["search_turns named"] == {"turns": [TURNS[0]]}
        for name in ["search_records", "every search_records"]:
            hits = after[name]["hits"]
            assert [(hit["turn"], hit["seq"]) for hit in hits] == [(1, 0)]
            assert hits[0]["created_at"] == HEADERS[0]["created_at"]
            assert hits[0]["actor"] == ACTOR
        ranked = after["search_records ranked"]["hits"]
        assert [(hit["turn"], hit["seq"]) for hit in ranked] == [(1, 0)]
        assert after["listing"] == {"conversations": [SESSION]}


def test_a_staged_restore_beside_a_visible_conversation_stays_out_of_its_answers(
    records: Server,
) -> None:
    """A store-wide search and listing answer the visible conversation
    alone while another holding the same words is being restored."""
    said = [{"role": "user", "content": WORD + " cache"}]
    turn = records.post(
        "conversation/append_turn", conversation_id="seen", messages=said
    )["turn"]
    part(records, SESSION, ID, **FIRST)
    part(records, SESSION, ID, **SECOND)
    found = seen(records, SESSION)
    assert found["search_turns"] == {"turns": [turn]}
    assert found["search_turns ranked"]["turns"] == [turn]
    hits = found["every search_records"]["hits"]
    assert [(hit["conversation_id"], hit["turn"]) for hit in hits] == [("seen", 1)]
    assert found["listing"] == {"conversations": ["seen"]}
    for name in ["read_turns", "read_records", "last", "projections", "trail"]:
        assert found[name] == ABSENT[name], name
    assert found["search_turns named"] == {"turns": []}
    assert found["search_records"] == {"hits": []}


def swept(conversation: str, restores: int, freed: int) -> dict[str, Any]:
    return {
        "conversation_id": conversation,
        "drafts": 0,
        "restores": restores,
        "freed_bytes": freed,
    }


def test_a_stale_staged_restore_is_swept_over_http(enabled: Binary) -> None:
    """Items 12 and 13: a restore opened over the wire and left for longer
    than `NEOSIAN_DRAFT_SECONDS` is previewed and removed by the sweep with
    every byte it held; a fresh one and a committed one are left alone."""
    home = enabled.home
    with serve(enabled) as server:
        part(server, "w-stale", "left", **FIRST)
        more = part(server, "w-stale", "left", turns=HEADERS[1:2])["stored_bytes"]
        held = content_bytes("w-stale", "left", FIRST) + more
        held += restore_bookkeeping("w-stale", "left")
        part(server, "w-fresh", "going", **FIRST)
        part(server, "w-done", "landed", **FIRST, commit=True)
        assert sweep(server) == NOTHING
        aged(home, "w-stale", "left")
        rows = [swept("w-stale", 1, held)]
        assert sweep(server)["conversations"] == rows
        assert sweep(server)["conversations"] == rows, "a preview removes nothing"
        at = position(2, 0, True)
        assert status(server, "w-stale", "left") == answer("left", "open", at)
        removed = sweep(server, write=True)
        assert removed == {"conversations": rows, "next_cursor": None, "write": True}
        assert sweep(server) == NOTHING
        assert status(server, "w-stale", "left") == answer("left", "none", None)
        assert not (home / ".records" / "restore" / "w-stale" / "left").exists()
        # What was not stale is where it was.
        assert status(server, "w-fresh", "going")["state"] == "open"
        assert status(server, "w-done", "landed")["state"] == "committed"
        assert server.post("store/conversations") == {"conversations": ["w-done"]}
        # The id and the target are free again.
        again = part(server, "w-stale", "left", **FIRST)
        assert again["stored_bytes"] == held - more
        assert again["freed_bytes"] == 0


def test_erasing_the_whole_conversation_removes_its_staged_restore(
    records: Server,
) -> None:
    """Item 12 "Discard": erasure of the whole conversation drops what is
    staged for it, and the id and the target are free again."""
    part(records, SESSION, ID, **FIRST)
    erased = records.post("conversation/redact_turns", conversation_id=SESSION)
    assert erased == {"count": 0}
    assert status(records, SESSION, ID) == answer(ID, "none", None)
    assert part(records, SESSION, ID, **FIRST)["stored_bytes"] == 4_002
    # Erasing some turns of a conversation that has none leaves it staged.
    records.post("conversation/redact_turns", conversation_id=SESSION, turns=[1])
    assert status(records, SESSION, ID)["state"] == "open"


def test_a_probe_is_not_a_touch_and_a_part_is(enabled: Binary) -> None:
    """Ruling R19 for a restore: a probe changes nothing, the restore's age
    included, so a stale restore that is only probed is still swept and
    still gives way to another opening; any part, a pure repeat included,
    makes it fresh."""
    home = enabled.home
    with serve(enabled) as server:
        part(server, SESSION, ID, **FIRST)
        aged(home, SESSION, ID)
        state = home / ".records" / "restore" / SESSION / ID / "state"
        before = state.read_bytes()
        at = position(1, 3, True)
        assert status(server, SESSION, ID) == answer(ID, "open", at)
        assert state.read_bytes() == before
        assert sweep(server)["conversations"] == [swept(SESSION, 1, 4_002)]
        # A repeat of what is stored adds nothing and touches.
        assert part(server, SESSION, ID, **FIRST) == answer(ID, "open", at)
        assert state.read_bytes() != before
        assert sweep(server) == NOTHING
        details = conflict(server, SESSION, "restore_open", restore="other", **FIRST)
        assert details == {"restore": ID}
        # Probed alone once more, it gives way to another opening.
        aged(home, SESSION, ID)
        status(server, SESSION, ID)
        other = part(server, SESSION, "other", **FIRST)
        assert other["freed_bytes"] == 4_002
        assert status(server, SESSION, ID) == answer(ID, "none", None)


def test_a_restored_conversation_reads_as_its_source(enabled: Binary) -> None:
    """A conversation with a turn written in parts, a turn stored whole, an
    erased turn, projections and a trail is read through `read_records`,
    sent to another target in parts of a few records and read back equal
    through `read_turns` and `read_records`."""
    source, target, name = "src-1", "dst-1", "move"
    with serve(enabled) as server:
        write(server, source, "d1", EXAMPLE, seal=True, actor=ACTOR)
        said = [{"role": "user", "content": "héllo\nworld"}]
        server.post("conversation/append_turn", conversation_id=source, messages=said)
        server.post(
            "conversation/append_turn",
            conversation_id=source,
            messages=[{"role": "user", "content": "a secret"}],
        )
        server.post("conversation/redact_turns", conversation_id=source, turns=[3])
        logs = [projection(1, "log", "ran the tests"), projection(3, "epoch", "", 3)]
        server.post(
            "conversation/append_projections", conversation_id=source, entries=logs
        )
        page = read(server, source, kinds=_ALL)
        assert page["continuation"] is None
        headers, answered = page["turns"], page["records"]
        assert [(h["records"], h["redacted"]) for h in headers] == [
            (16, False),
            (1, False),
            (0, True),
        ]
        items = [
            {key: value for key, value in item.items() if key not in _ANSWER_ONLY}
            for item in answered
        ]
        trail = server.post("conversation/turn_redactions", conversations=[source])
        acts = [{**one, "conversation_id": target} for one in trail["redactions"]]
        assert len(acts) == 1
        # Headers first, then the records five at a time, then the rest.
        stream = _stream(headers, items) + logs + acts
        part(server, target, name, turns=headers[:1])
        for start in range(0, 16, 5):
            part(server, target, name, records=items[start:16][:5])
        assert [item["turn"] for item in items] == [1] * 16 + [2]
        part(server, target, name, turns=headers[1:], records=items[16:])
        done = part(
            server,
            target,
            name,
            projections_from=0,
            projections=logs,
            redactions_from=0,
            redactions=acts,
            commit=True,
        )
        assert done["digest"] == unit_digest(stream)
        assert done["position"] == position(3, 0, True, 2, 1)

        def turns(conversation: str) -> list[dict[str, Any]]:
            body = server.post("conversation/read_turns", conversation_id=conversation)
            assert body["next_after"] is None
            return [{**turn, "conversation_id": "-"} for turn in body["turns"]]

        assert turns(target) == turns(source)
        assert turns(target)[0]["messages"] == MESSAGES
        assert read(server, target, kinds=_ALL) == page
        assert server.post(
            "conversation/read_projections", conversation_id=target
        ) == server.post("conversation/read_projections", conversation_id=source)


_ALL = [
    "prompt",
    "reply",
    "system",
    "tool_result",
    "tool_call",
    "reasoning",
    "attachment",
    "extra",
]
_ANSWER_ONLY = ("tail", "truncated")


def _stream(
    headers: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Each header followed by its turn's records, as a restore sends them."""
    ordered: list[dict[str, Any]] = []
    for head in headers:
        ordered.append(head)
        ordered.extend(item for item in items if item["turn"] == head["turn"])
    return ordered
