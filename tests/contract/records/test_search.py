"""`conversation/search_records`, its request (DESIGN §40.54 items 8.2 and
11): every field of the table with its default, its bounds and its refusal,
the candidate bound counted in records, and who may search what. A client
outside its allowance or its grants is refused before the store is touched:
the refusal arrives while this test holds the store's lock."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.records.searching import (
    ABSENT,
    SEARCH,
    SEARCHABLE,
    UNGRANTED,
    WHOLE_STORE,
    forbidden,
    message,
    places,
    post,
    refusal,
    search,
    store_lock,
)
from tests.contract.records.support import record, write
from tests.contract.support import ADMIN, LIMITED, Binary, Server, serve

QUERY = "query requires 1..1024 characters and 1..16 whitespace terms"
LIMIT = "limit must be between 1 and 100"
BROAD = (
    "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than 3 records"
    " hold a search term; narrow the words, the kinds or the time range"
)
GRANTS = """grant_format = 1
[[clients]]
actor = "client:contract"
operator = true
[[clients.conversations]]
prefix = "g-"
read = true
append = true
[[clients]]
actor = "client:reader"
[[clients.conversations]]
ids = ["g-1", "g-3"]
read = true
[[clients]]
actor = "client:writer"
[[clients.conversations]]
prefix = "g-"
append = true
"""


def said(server: Server, conversation: str, *texts: str) -> dict[str, Any]:
    """One sealed turn of prompts, a record each; its header."""
    items = [
        record(index, index, "user", "prompt", text) for index, text in enumerate(texts)
    ]
    draft = f"d{int(time.time_ns())}"
    sealed: dict[str, Any] = write(server, conversation, draft, items, seal=True)
    return dict(sealed["turn"])


def test_every_field_has_its_default_and_takes_its_bounds(records: Server) -> None:
    said(records, "s-1", "needle one", "hay", "needle two")
    said(records, "s-2", "needle three")
    everything = [("s-2", 1, 0), ("s-1", 1, 0), ("s-1", 1, 2)]
    assert places(search(records, "needle")) == everything
    spelled = search(
        records,
        "needle",
        conversations=["s-1", "s-2"],
        kinds=SEARCHABLE,
        order="newest",
        limit=20,
    )
    assert places(spelled) == everything and spelled["order"] == "newest"
    # `conversations` and `kinds` are sets: order and repeats do not matter.
    again = search(
        records,
        "needle",
        conversations=["s-2", "s-1", "s-2", "s-1"],
        kinds=["prompt"] * 50_000 + ["reply"] * 50_000,
    )
    assert places(again) == everything
    assert places(search(records, "needle", conversations=["s-1"])) == everything[1:]
    assert search(records, "needle", conversations=[]) == {"hits": []}
    assert search(records, "needle", kinds=[]) == {"hits": []}
    assert search(records, "needle", kinds=["reply", "system"]) == {"hits": []}
    # A conversation that does not exist holds no hit.
    assert search(records, "needle", conversations=["ghost"]) == {"hits": []}
    assert places(search(records, "needle", conversations=["ghost", "s-2"])) == [
        ("s-2", 1, 0)
    ]
    # Terms are folded and a repeated term counts once; newest needs each.
    assert places(search(records, "NEEDLE  needle\tNeedle")) == everything
    assert places(search(records, "needle two")) == [("s-1", 1, 2)]
    assert places(search(records, "two needle", limit=1)) == [("s-1", 1, 2)]
    assert places(search(records, "needle", limit=1)) == everything[:1]
    assert places(search(records, "needle", limit=100)) == everything
    assert places(search(records, "x" * 1_024)) == []
    assert places(search(records, " ".join(f"t{n}" for n in range(16)))) == []
    ranked = search(records, "needle", order="relevance")
    assert ranked["order"] == "relevance" and len(ranked["hits"]) == 3


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({}, "missing parameter: 'query'"),
        ({"query": None}, "parameter 'query' must be a string"),
        ({"query": 7}, "parameter 'query' must be a string"),
        ({"query": ["needle"]}, "parameter 'query' must be a string"),
        ({"query": ""}, QUERY),
        ({"query": " \n\t "}, QUERY),
        ({"query": "x" * 1_025}, QUERY),
        ({"query": "é" * 1_025}, QUERY),
        ({"query": " ".join(["t"] * 17)}, QUERY),
        (
            {"query": "x", "conversations": None},
            "parameter 'conversations' must be a list",
        ),
        (
            {"query": "x", "conversations": "s-1"},
            "parameter 'conversations' must be a list",
        ),
        (
            {"query": "x", "conversations": ["s-1", 7]},
            "parameter 'conversations' must be a list of ids",
        ),
        (
            {"query": "x", "conversations": [None]},
            "parameter 'conversations' must be a list of ids",
        ),
        ({"query": "x", "kinds": None}, "parameter 'kinds' must be a list"),
        ({"query": "x", "kinds": "prompt"}, "parameter 'kinds' must be a list"),
        ({"query": "x", "kinds": [1]}, "parameter 'kinds' must be a list of kinds"),
        ({"query": "x", "kinds": ["prompts"]}, 'unknown record kind "prompts"'),
        ({"query": "x", "kinds": ["reasoning"]}, 'kind "reasoning" is not searchable'),
        (
            {"query": "x", "kinds": ["attachment"]},
            'kind "attachment" is not searchable',
        ),
        (
            {"query": "x", "kinds": ["prompt", "extra"]},
            'kind "extra" is not searchable',
        ),
        ({"query": "x", "order": None}, "parameter 'order' must be a string"),
        ({"query": "x", "order": 1}, "parameter 'order' must be a string"),
        ({"query": "x", "order": "path"}, "order must be newest or relevance"),
        ({"query": "x", "order": "NEWEST"}, "order must be newest or relevance"),
        ({"query": "x", "since": None}, "parameter 'since' must be a string"),
        ({"query": "x", "before": 1}, "parameter 'before' must be a string"),
        (
            {"query": "x", "since": "2026-11-21T10:00:00+00:00"},
            "since must be a UTC RFC 3339 instant ending in Z",
        ),
        (
            {"query": "x", "since": "2026-11-21"},
            "since must be a UTC RFC 3339 instant ending in Z",
        ),
        (
            {"query": "x", "before": "yesterday"},
            "before must be a UTC RFC 3339 instant ending in Z",
        ),
        (
            {
                "query": "x",
                "since": "2026-11-21T10:00:00Z",
                "before": "2026-11-21T10:00:00Z",
            },
            "since must precede before",
        ),
        (
            {
                "query": "x",
                "since": "2026-11-22T00:00:00Z",
                "before": "2026-11-21T00:00:00Z",
            },
            "since must precede before",
        ),
        (
            {"query": "x", "limit": None},
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            {"query": "x", "limit": "5"},
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            {"query": "x", "limit": 1.5},
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            {"query": "x", "limit": True},
            "parameter 'limit' must be a non-negative integer",
        ),
        (
            {"query": "x", "limit": -1},
            "parameter 'limit' must be a non-negative integer",
        ),
        ({"query": "x", "limit": 0}, LIMIT),
        ({"query": "x", "limit": 101}, LIMIT),
        ({"query": "x", "limit": 1 << 63}, LIMIT),
        # Nothing of `search_turns`, of paging or of another route is read.
        (
            {"query": "x", "conversation_id": "s-1"},
            "unknown parameter: 'conversation_id'",
        ),
        ({"query": "x", "scope": "user:a"}, "unknown parameter: 'scope'"),
        ({"query": "x", "page_bytes": 1}, "unknown parameter: 'page_bytes'"),
        ({"query": "x", "continuation": "a"}, "unknown parameter: 'continuation'"),
        ({"query": "x", "cursor": None}, "unknown parameter: 'cursor'"),
        ({"query": "x", "heads": {}}, "unknown parameter: 'heads'"),
        ({"query": "x", "Query": "x"}, "unknown parameter: 'Query'"),
    ],
)
def test_each_rule_of_the_table_refuses_in_its_own_words(
    records: Server, body: dict[str, Any], why: str
) -> None:
    said(records, "s-1", "x marks it")
    assert message(records, SEARCH, body) == why


def test_an_id_outside_the_grammar_is_refused_by_its_own_code(records: Server) -> None:
    said(records, "s-1", "needle")
    for bad in ["has space", "", "..", "a/b", "x" * 129]:
        error = refusal(
            records,
            SEARCH,
            {"query": "needle", "conversations": ["s-1", bad]},
            400,
            "agent_conversation_id_invalid",
        )
        assert error["details"]["conversation_id"] == bad


def test_a_store_without_records_answers_its_own_refusal(server: Server) -> None:
    """Ruling D6: the store's refusal to every body that is an object,
    before any field is read; the allowance still comes first."""
    server.post(
        "conversation/append_turn",
        conversation_id="allowed-1",
        messages=[{"role": "user", "content": "needle"}],
    )
    for body in [
        {"query": "needle"},
        {"query": "needle", "conversations": ["allowed-1"], "order": "relevance"},
        {},
        {"query": 7, "limit": 0, "nonsense": True},
    ]:
        assert refusal(server, SEARCH, body, 400, "agent_configuration_error") == ABSENT
    inside = {"query": "needle", "conversations": ["allowed-1"]}
    limited = refusal(
        server, SEARCH, inside, 400, "agent_configuration_error", token=LIMITED
    )
    assert limited == ABSENT
    assert forbidden(server, SEARCH, {"query": "needle"}, LIMITED) == WHOLE_STORE
    not_json = server.request("/v1/" + SEARCH, raw=b"needle")
    assert (not_json.status, not_json.body["error"]["message"]) == (
        400,
        "request body must be JSON",
    )


def test_relevance_refuses_past_the_candidate_bound_counted_in_records(
    enabled: Binary,
) -> None:
    """One turn of four records holding the term is four candidates, where
    `search_turns` counts one turn. Past the bound the search is refused,
    never cut short; newest keeps no statistics and is not bounded."""
    with serve(enabled, extra_env={"NEOSIAN_SEARCH_MAX_CANDIDATES": "3"}) as server:
        items = [
            record(0, 0, "user", "prompt", "needle a"),
            record(1, 1, "assistant", "reply", "needle b"),
            record(2, 2, "user", "prompt", "needle c"),
            record(3, 3, "user", "prompt", "needle d"),
            record(4, 4, "user", "prompt", "hay"),
        ]
        write(server, "s-broad", "d", items, seal=True)
        narrower: list[dict[str, Any]] = [
            {},
            {"limit": 1},
            {"conversations": ["s-broad"]},
        ]
        for more in narrower:
            body = {"query": "needle", "order": "relevance", **more}
            assert message(server, SEARCH, body) == BROAD
        assert (
            message(server, SEARCH, {"query": "needle hay", "order": "relevance"})
            == BROAD
        )
        newest = search(server, "needle", order="newest")
        assert [found["seq"] for found in newest["hits"]] == [0, 1, 2, 3]
        narrowed = search(server, "needle", order="relevance", kinds=["prompt"])
        assert sorted(found["seq"] for found in narrowed["hits"]) == [0, 2, 3]
        turns = server.post(
            "conversation/search_turns", query="needle", order="relevance"
        )
        assert [turn["turn"] for turn in turns["turns"]] == [1]


def test_a_constrained_token_names_the_conversations_it_searches(
    records: Server,
) -> None:
    """Item 8.2: read permission on every conversation named, and the list
    is what names them. Nothing else in the body stands in for it, and a
    refusal says nothing of what the store holds."""
    said(records, "allowed-1", "needle inside")
    said(records, "allowed-2", "needle beside")
    said(records, "other-1", "needle secret")
    inside = post(
        records,
        SEARCH,
        {"query": "needle", "conversations": ["allowed-1"]},
        token=LIMITED,
    )
    assert inside.status == 200 and places(inside.body) == [("allowed-1", 1, 0)]
    both = {"query": "needle", "conversations": ["allowed-2", "allowed-1", "allowed-9"]}
    assert places(post(records, SEARCH, both, token=LIMITED).body) == [
        ("allowed-2", 1, 0),
        ("allowed-1", 1, 0),
    ]
    none = post(
        records, SEARCH, {"query": "needle", "conversations": []}, token=LIMITED
    )
    assert (none.status, none.body) == (200, {"hits": []})
    outside = "this token's allowance does not reach conversation 'other-1'"
    for body, why in [
        ({"query": "needle"}, WHOLE_STORE),
        ({"query": "needle", "conversations": None}, WHOLE_STORE),
        ({"query": "needle", "conversations": "allowed-1"}, WHOLE_STORE),
        ({"query": "needle", "conversation_id": "allowed-1"}, WHOLE_STORE),
        ({"query": "needle", "scope": "user:allowed"}, WHOLE_STORE),
        ({"conversation_id": "allowed-1", "scope": "user:allowed"}, WHOLE_STORE),
        ({}, WHOLE_STORE),
        ({"query": "needle", "conversations": ["other-1"]}, outside),
        ({"query": "needle", "conversations": ["allowed-1", "other-1"]}, outside),
        ({"query": "", "limit": 0, "conversations": ["other-1"]}, outside),
        (
            {"query": "needle", "conversations": ["ghost"]},
            "this token's allowance does not reach conversation 'ghost'",
        ),
    ]:
        assert forbidden(records, SEARCH, body, LIMITED) == why, body
    # The strict body still binds a token inside its allowance.
    extra = {"query": "needle", "conversations": ["allowed-1"], "scope": "user:allowed"}
    strict = post(records, SEARCH, extra, token=LIMITED)
    assert (strict.status, strict.body["error"]["message"]) == (
        400,
        "unknown parameter: 'scope'",
    )
    # A token with no allowance searches every conversation.
    assert places(search(records, "needle")) == [
        ("other-1", 1, 0),
        ("allowed-2", 1, 0),
        ("allowed-1", 1, 0),
    ]


def granted(tmp_path: Path) -> dict[str, str]:
    grants = tmp_path / "grants.toml"
    grants.write_text(GRANTS, encoding="utf-8")
    return {
        "NEOSIAN_SERVE_GRANTS": str(grants),
        "NEOSIAN_SERVE_TOKEN": (
            f"client:contract={ADMIN},client:reader=reader,client:writer=writer,"
            "client:stranger=stranger"
        ),
    }


def test_grants_need_read_on_every_conversation_named(
    enabled: Binary, tmp_path: Path
) -> None:
    """Under grants the list is required of every client, the operator
    included (ruling R13), and each id needs `read`."""
    with serve(enabled, extra_env=granted(tmp_path)) as server:
        for conversation in ["g-1", "g-2", "g-3"]:
            said(server, conversation, "needle " + conversation)
        one = post(
            server,
            SEARCH,
            {"query": "needle", "conversations": ["g-1", "g-3"]},
            token="reader",
        )
        assert one.status == 200
        assert places(one.body) == [("g-3", 1, 0), ("g-1", 1, 0)]
        for token in ["reader", "writer"]:
            empty = post(
                server, SEARCH, {"query": "needle", "conversations": []}, token=token
            )
            assert (empty.status, empty.body) == (200, {"hits": []})
        operator = search(server, "needle", conversations=["g-1", "g-2", "g-3"])
        assert len(operator["hits"]) == 3
        for token, body in [
            ("reader", {"query": "needle", "conversations": ["g-2"]}),
            ("reader", {"query": "needle", "conversations": ["g-1", "g-2"]}),
            ("reader", {"query": "needle", "conversations": ["g-9"]}),
            ("reader", {"query": "needle", "conversations": ["g-1", 7]}),
            ("reader", {"query": "needle"}),
            ("reader", {"query": "needle", "conversation_id": "g-1"}),
            ("reader", {"query": 7, "conversations": ["g-2"], "nonsense": 1}),
            ("writer", {"query": "needle", "conversations": ["g-1"]}),
            ("stranger", {"query": "needle", "conversations": ["g-1"]}),
            ("stranger", {"query": "needle", "conversations": []}),
            (ADMIN, {"query": "needle"}),
            (ADMIN, {"query": "needle", "conversations": None}),
            (ADMIN, {"query": "needle", "conversations": ["g-1", "other-1"]}),
        ]:
            assert forbidden(server, SEARCH, body, token) == UNGRANTED, (token, body)


def test_a_refused_search_never_waits_for_the_store(
    enabled: Binary, tmp_path: Path
) -> None:
    """A search that may run waits for the store's lock, as every store
    operation does. One that is refused for its allowance or its grants
    answers while the lock is held: nothing was opened for it."""
    with serve(enabled, extra_env=granted(tmp_path)) as server:
        said(server, "g-1", "needle one")
        said(server, "g-2", "needle two")
        allowed = {"query": "needle", "conversations": ["g-1"]}
        answers: list[Any] = []

        def ask() -> None:
            answers.append(post(server, SEARCH, allowed, token="reader"))

        waiting = threading.Thread(target=ask)
        with store_lock(enabled.home):
            started = time.monotonic()
            for token, body in [
                ("reader", {"query": "needle", "conversations": ["g-2"]}),
                ("reader", {"query": "needle", "conversations": ["g-1", "g-2"]}),
                ("reader", {"query": "needle"}),
                ("writer", allowed),
                ("stranger", allowed),
                (ADMIN, {"query": "needle"}),
            ]:
                assert forbidden(server, SEARCH, body, token) == UNGRANTED
            assert time.monotonic() - started < 5
            waiting.start()
            waiting.join(0.5)
            assert waiting.is_alive() and answers == []
        waiting.join(10)
        assert not waiting.is_alive()
        assert answers[0].status == 200 and places(answers[0].body) == [("g-1", 1, 0)]
    with serve(enabled) as server, store_lock(enabled.home):
        for body in [
            {"query": "needle"},
            {"query": "needle", "conversations": ["other-1"]},
            {"query": "needle", "conversation_id": "allowed-1"},
        ]:
            reply = post(server, SEARCH, body, token=LIMITED)
            assert (reply.status, reply.body["error"]["code"]) == (403, "forbidden")
