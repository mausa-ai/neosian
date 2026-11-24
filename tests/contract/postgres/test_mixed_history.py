"""Existing search_turns and search CLI contracts over a PostgreSQL records extension."""

import json
from typing import Any

import pytest

from tests.contract.file.test_records import record
from tests.contract.file.test_records_search import LIMIT, SESSION, numbers, pages
from tests.contract.postgres.history_fixture import native, plant_mixed, postgres
from tests.contract.postgres.test_records_extension import Database, install
from tests.contract.postgres.test_search_bounds import search
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres
SEARCH = "conversation/search_turns"
PATH = "/v1/" + SEARCH
CAP = "NEOSIAN_SEARCH_MAX_CANDIDATES"


def turns(server: Server, query: str = "alpha", **body: Any) -> Any:
    return server.post(SEARCH, query=query, **body)


def test_whole_turns_and_scores_across_formats(
    binary: Binary, database: Database
) -> None:
    plant_mixed(binary, database)
    with postgres(binary, database) as server:
        version_one = turns(server)
        assert list(version_one) == ["turns"]
        assert numbers(version_one) == [4, 3, 2, 1]
        assert [len(t["messages"][0]["content"]) for t in version_one["turns"]] == [
            10,
            11,
            70_000,
            9,
        ]
        ranked = turns(server, order="relevance")
        assert list(zip(numbers(ranked), ranked["scores"], strict=True)) == [
            (1, 178239),
            (4, 178232),
            (3, 178225),
            (2, 47320),
        ]
        window = {"since": "2026-11-21T10:01:00Z", "before": "2026-11-21T10:03:00Z"}
        selected = turns(
            server, conversations=[SESSION, SESSION, "absent"], order="newest", **window
        )
        assert numbers(selected) == [3, 2]
        assert not numbers(turns(server, conversations=["absent"]))
    for args, expected in [
        (("--order", "newest"), [4, 3, 2, 1]),
        (
            (
                "--order",
                "newest",
                "--since",
                window["since"],
                "--before",
                window["before"],
            ),
            [3, 2],
        ),
        (("--order", "relevance"), [1, 4, 3, 2]),
    ]:
        result = search(binary, database[1], "alpha", "--conversation", SESSION, *args)
        assert result.returncode == 0, result.stderr
        assert [hit["turn"] for hit in json.loads(result.stdout)["hits"]] == expected


def test_oversize_turn_occupies_its_hit_slot_and_reports_omitted(
    binary: Binary, database: Database
) -> None:
    plant_mixed(binary, database)
    with postgres(binary, database, **LIMIT) as server:
        full = turns(server)
        assert (numbers(full), list(full), full["omitted"]) == (
            [4, 3, 1],
            ["turns", "omitted"],
            1,
        )
        for order, expected, omitted in [
            ({}, [[4], [3], [1]], [0, 1, 0]),
            ({"order": "newest"}, [[4], [3], [1]], [0, 1, 0]),
            ({"order": "relevance"}, [[1], [4], [3]], [0, 0, 1]),
        ]:
            answers = pages(server, **order)
            assert [numbers(a) for a in answers] == expected
            assert [a.get("omitted", 0) for a in answers] == omitted
            assert all(list(a)[-1] == "omitted" for a in answers if a.get("omitted"))
        limited = pages(server, limit=3)
        assert [numbers(a) for a in limited] == [[4], [3]]
        assert [a.get("omitted", 0) for a in limited] == [0, 1]
        ranked = turns(server, order="relevance")
        assert list(zip(numbers(ranked), ranked["scores"], strict=True)) == [
            (1, 178239),
            (4, 178232),
            (3, 178225),
        ]
        assert turns(
            server, query="xxxx", order="relevance", page_bytes=1, limit=1
        ) == {
            "turns": [],
            "order": "relevance",
            "scores": [],
            "continuation": None,
            "omitted": 1,
        }


def test_legacy_limit_counts_candidate_turns_before_ranking(
    binary: Binary, database: Database
) -> None:
    plant_mixed(binary, database)
    with postgres(binary, database, **{CAP: "4"}) as server:
        # Both physical chunks of turn 2 hold xxx; four turns hold either word.
        assert sorted(numbers(turns(server, "alpha xxx", order="relevance"))) == [
            1,
            2,
            3,
            4,
        ]
    with postgres(binary, database, **{CAP: "3"}) as server:
        refusal = server.request(
            PATH, {"query": "alpha xxx", "order": "relevance", "limit": 1}
        )
        assert refusal.status == 400
        assert refusal.body == {
            "error": {
                "code": "value_error",
                "message": "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than 3 turns "
                "hold a search term; narrow the words or the time range",
                "details": {},
            }
        }
        assert numbers(turns(server, "alpha xxx", order="newest")) == [2]
        assert numbers(
            turns(server, order="relevance", since="2026-11-21T10:03:00Z")
        ) == [4]


def test_resume_key_and_relevance_digest_with_new_mixed_candidate(
    binary: Binary, database: Database
) -> None:
    plant_mixed(binary, database)
    with postgres(binary, database) as server:
        newest = turns(server, page_bytes=1)
        ranked = turns(server, order="relevance", page_bytes=1)
        assert numbers(newest) == [4] and numbers(ranked) == [1]
        native(
            database,
            SESSION,
            5,
            "2026-11-21T10:04:00Z",
            [record(0, 0, "user", "prompt", "alpha later")],
        )
        assert numbers(turns(server)) == [5, 4, 3, 2, 1]
        resumed = turns(server, page_bytes=200_000, continuation=newest["continuation"])
        assert (numbers(resumed), resumed["continuation"]) == ([3, 2, 1], None)
        stale = server.request(
            PATH,
            {
                "query": "alpha",
                "order": "relevance",
                "page_bytes": 1,
                "continuation": ranked["continuation"],
            },
        )
        assert stale.status == 400
        assert stale.body["error"]["code"] == "value_error"
        assert (
            stale.body["error"]["message"]
            == "page continuation is stale: the candidate turns changed since the first page; search again"
        )


def test_history_words_span_pieces_and_keep_the_legacy_query_domain(
    binary: Binary, database: Database
) -> None:
    install(database, binary)
    words = [f"word{i:02d}" for i in range(19)]
    long = "z" * 1025
    left, right = " ".join(words[:9]), " ".join(words[9:])
    # Each term may match a different piece of the same turn.
    native(
        database,
        "many",
        1,
        "2026-11-21T10:00:00Z",
        [
            record(0, 0, "user", "prompt", f"north {left} edgeAW"),
            record(1, 1, "assistant", "reply", f"AY {right} south {long} literal%_"),
        ],
    )
    native(
        database,
        "other",
        1,
        "2026-11-21T10:00:00Z",
        [record(0, 0, "user", "prompt", "north solo literalXX")],
    )
    # A full 65,536-byte JSON string field cuts the word needle in two.
    prefix = "a" * 65_531 + "nee"
    native(
        database,
        "cut",
        1,
        "2026-11-21T10:00:00Z",
        [
            record(0, 0, "user", "prompt", prefix, size=len(prefix) + 3),
            record(
                1,
                0,
                "user",
                "prompt",
                "dle",
                size=len(prefix) + 3,
                chunk=1,
                content=None,
            ),
        ],
    )
    with postgres(binary, database) as server:
        server.post(
            "conversation/append_turn",
            conversation_id="old",
            messages=[{"role": "user", "content": f"{left} {right} {long}"}],
        )
        for query in [" ".join(words), long]:
            for order in ["newest", "relevance"]:
                result = turns(server, query, order=order)
                assert sorted(t["conversation_id"] for t in result["turns"]) == [
                    "many",
                    "old",
                ]
        first = turns(server, "north", page_bytes=1)
        second = turns(
            server, "north", page_bytes=1, continuation=first["continuation"]
        )
        assert [a["turns"][0]["conversation_id"] for a in (first, second)] == [
            "other",
            "many",
        ]
        assert second["continuation"] is None
        assert numbers(turns(server, "north south", conversations=["many"])) == [1]
        assert not numbers(turns(server, "solo south"))
        assert not numbers(turns(server, "edgeAWAY"))
        for order in ["newest", "relevance"]:
            assert not numbers(turns(server, "needle", order=order))
            assert numbers(
                turns(server, "nee dle", conversations=["cut"], order=order)
            ) == [1]
        # Membership uses individual SQL records; once "nee" selects the turn,
        # its whole searchable text contains both "nee" and "needle" once.
        selected = turns(server, "nee needle", conversations=["cut"], order="relevance")
        assert (numbers(selected), selected["scores"]) == ([1], [575364])
        assert [t["conversation_id"] for t in turns(server, "literal%_")["turns"]] == [
            "many"
        ]


def test_whole_format_keeps_its_original_tool_search_text_after_install(
    binary: Binary, database: Database
) -> None:
    # The old writer's rendering keeps the entered argument order even though
    # PostgreSQL JSONB reads the short key first.
    _, _, legacy = database
    query = 'yy","b'
    legacy.post(
        "conversation/append_turn",
        conversation_id="tool",
        messages=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "probe",
                        "arguments": {"aa": "yy", "b": "xx"},
                    }
                ],
            },
            {"role": "tool", "content": "probe result", "tool_call_id": "call-1"},
        ],
    )
    baseline = [
        turns(legacy, query, **order)
        for order in [{}, {"order": "newest"}, {"order": "relevance"}]
    ]
    assert [numbers(answer) for answer in baseline] == [[1], [1], [1]]
    assert baseline[-1]["scores"] == [287682]
    install(database, binary)
    with postgres(binary, database) as server:
        for order, expected in zip(
            [{}, {"order": "newest"}, {"order": "relevance"}], baseline, strict=True
        ):
            assert turns(server, query, **order) == expected
