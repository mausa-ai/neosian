"""`conversation/search_records` and `store/sweep_records` on Postgres with
records extension 1 (DESIGN §40.54 items 11 and 13): the design's example,
both orders with the literal scores of the FileStore contracts, what never
matches, and one sweep over drafts aged in the database. A sibling of
`test_records_wire.py`, whose server fixture it shares."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.contract.postgres.test_records_wire import Database, recording
from tests.contract.records.searching import (
    SEARCH,
    SWEEP,
    WHOLE_STORE,
    answer_bytes,
    cursor,
    forbidden,
    hit,
    message,
    places,
    post,
    ranked,
    raw,
    search,
    sweep,
)
from tests.contract.records.support import (
    bookkeeping,
    compact,
    probe,
    prompts,
    record,
    record_bytes,
    write,
)
from tests.contract.records.test_search_answers import example, hidden
from tests.contract.records.test_search_hits import (
    ALL_FIVE,
    PARTS,
    SESSION,
    TERMS,
    WHOLE,
    corpus,
)
from tests.contract.support import ADMIN, LIMITED, Binary

pytestmark = pytest.mark.postgres


def test_the_design_example_finds_the_failing_assertion(
    database: Database, binary: Binary
) -> None:
    """Item 11's example with the literals of the FileStore contract: the
    hit's place, its call's name, the excerpt from scalar 31,507 and the
    score of a lone candidate; a word across a cut is not found."""
    token = f"client:default={ADMIN}"
    with recording(database, binary, NEOSIAN_SERVE_TOKEN=token) as (server, _, _):
        example(server, files=False)


def test_both_orders_answer_the_literal_scores_and_bytes(
    database: Database, binary: Binary
) -> None:
    with recording(database, binary) as (server, _, _):
        first, second = corpus(server)
        newest = [
            hit(SESSION, second, PARTS[2], TERMS),
            hit(SESSION, second, PARTS[4], TERMS, name="deploy_cache"),
            hit(SESSION, first, WHOLE[0], TERMS),
            hit(SESSION, first, WHOLE[1], TERMS),
        ]
        assert search(server, "deploy CACHE") == {"hits": newest}
        assert raw(server, SEARCH, {"query": "deploy CACHE", "order": "newest"}) == (
            200,
            answer_bytes(newest, "newest"),
        )
        best = search(server, "deploy CACHE", order="relevance")
        assert ranked(best) == ALL_FIVE
        assert list(best["hits"][0])[-1] == "score"
        new = second["created_at"]
        bounded = search(server, "deploy cache", order="relevance", since=new)
        assert ranked(bounded) == [
            (2, 4, 760961),
            (2, 2, 734682),
            (2, 1, 405460),
            (2, 0, 358400),
        ]
        only = search(server, "cache", order="relevance", kinds=["tool_call"])
        assert ranked(only) == [(2, 2, 395563)]
        assert search(server, "deploy", kinds=[]) == {"hits": []}
        assert search(server, "deploy", conversations=[]) == {"hits": []}
        assert message(server, SEARCH, {"query": "x", "kinds": ["extra"]}) == (
            'kind "extra" is not searchable'
        )
        assert message(server, SEARCH, {"query": "x", "page_bytes": 1}) == (
            "unknown parameter: 'page_bytes'"
        )
        assert message(server, SEARCH, {"query": "x", "limit": 101}) == (
            "limit must be between 1 and 100"
        )
        # The allowance: the list is what names the conversations.
        write(server, "allowed-1", "d", prompts(1), seal=True)
        inside = post(
            server,
            SEARCH,
            {"query": "a", "conversations": ["allowed-1"]},
            token=LIMITED,
        )
        assert inside.status == 200 and places(inside.body) == [("allowed-1", 1, 0)]
        for body in [
            {"query": "a"},
            {"query": "a", "conversation_id": "allowed-1"},
            {"query": "a", "scope": "user:allowed"},
        ]:
            assert forbidden(server, SEARCH, body, LIMITED) == WHOLE_STORE
        outside = {"query": "deploy", "conversations": ["allowed-1", SESSION]}
        assert forbidden(server, SEARCH, outside, LIMITED) == (
            f"this token's allowance does not reach conversation '{SESSION}'"
        )


def test_a_draft_and_a_redacted_turn_never_match(
    database: Database, binary: Binary
) -> None:
    with recording(database, binary) as (server, _, _):
        hidden(server)


def test_relevance_refuses_past_the_candidate_bound(
    database: Database, binary: Binary
) -> None:
    low = {"NEOSIAN_SEARCH_MAX_CANDIDATES": "3"}
    with recording(database, binary, **low) as (server, _, _):
        write(server, "s-broad", "d", prompts(4), seal=True)
        assert message(server, SEARCH, {"query": "a", "order": "relevance"}) == (
            "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than 3"
            " records hold a search term; narrow the words, the kinds or the"
            " time range"
        )
        assert len(search(server, "a")["hits"]) == 4


def age(
    connection: psycopg.Connection[tuple[object, ...]], name: str, conversation: str
) -> None:
    """Date a conversation's open drafts to the year 2000."""
    query = psycopg.sql.SQL(
        "UPDATE {}.record_drafts SET touched_at = '2000-01-01T00:00:00Z'"
        " WHERE conversation_id = %s AND state = 'open'"
    ).format(psycopg.sql.Identifier(name))
    assert connection.execute(query, (conversation,)).rowcount >= 1


def test_stale_drafts_are_previewed_then_swept_a_page_at_a_time(
    database: Database, binary: Binary
) -> None:
    """Item 13 on Postgres: three conversations with drafts aged in the
    database and one left fresh; preview equal to removal, the freed bytes
    of the size rule, pages by cursor, the cursor's method, the operator."""
    one = [record(0, 0, "user", "prompt", "alpha open")]
    with recording(database, binary) as (server, link, name):
        expected: list[dict[str, Any]] = []
        for conversation, drafts in [
            ("w-a", ["x"]),
            ("w-b", ["y", "zz"]),
            ("w-c", ["x"]),
        ]:
            freed = 0
            for draft in drafts:
                write(server, conversation, draft, one)
                freed += bookkeeping(conversation, draft)
                freed += record_bytes(conversation, one[0])
            age(link, name, conversation)
            expected.append(
                {
                    "conversation_id": conversation,
                    "drafts": len(drafts),
                    "restores": 0,
                    "freed_bytes": freed,
                }
            )
        assert [row["freed_bytes"] for row in expected] == [1_303, 2_607, 1_303]
        write(server, "w-d", "fresh", one)
        write(server, "w-e", "done", one, seal=True)
        preview = sweep(server)
        assert preview == {
            "conversations": expected,
            "next_cursor": None,
            "write": False,
        }
        assert raw(server, SWEEP, {}) == (200, compact(preview))
        first = sweep(server, limit=1)
        assert first == {
            "conversations": expected[:1],
            "next_cursor": cursor("sweep-v1", "w-a"),
            "write": False,
        }
        # The preview's cursor with `write: true` sweeps what lies after it.
        for body in [{}, {"write": True}, {"conversations": []}]:
            assert forbidden(server, SWEEP, body, LIMITED) == WHOLE_STORE
        foreign = cursor("sweep-v2", "w-a")
        assert message(server, SWEEP, {"cursor": foreign, "write": True}) == (
            f"malformed sweep-v1 cursor: {foreign!r}"
        )
        assert message(server, SWEEP, {"limit": 0}) == "sweep limit must be in 1..100"
        assert message(server, SWEEP, {"seconds": 0}) == "unknown parameter: 'seconds'"
        # Ruling R19: a probe is not a touch, so the probed drafts stay
        # stale; a repeat carrying records touches, and its draft is fresh.
        for conversation, draft in [("w-a", "x"), ("w-b", "y"), ("w-b", "zz")]:
            assert probe(server, conversation, draft)["next_seq"] == 1
        assert sweep(server) == preview
        write(server, "w-g", "again", one)
        age(link, name, "w-g")
        assert [row["conversation_id"] for row in sweep(server)["conversations"]] == [
            "w-a",
            "w-b",
            "w-c",
            "w-g",
        ]
        assert write(server, "w-g", "again", one)["stored_bytes"] == 0
        assert sweep(server) == preview
        second = sweep(server, limit=1, write=True, cursor=first["next_cursor"])
        assert second == {
            "conversations": expected[1:2],
            "next_cursor": cursor("sweep-v1", "w-b"),
            "write": True,
        }
        assert probe(server, "w-b", "y")["state"] == "none"
        rest = sweep(server, write=True)
        assert rest == {
            "conversations": [expected[0], expected[2]],
            "next_cursor": None,
            "write": True,
        }
        assert sweep(server) == {
            "conversations": [],
            "next_cursor": None,
            "write": False,
        }
        assert probe(server, "w-d", "fresh")["next_seq"] == 1
        assert places(search(server, "alpha")) == [("w-e", 1, 0)]
