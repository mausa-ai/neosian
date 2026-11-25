"""`store/sweep_records` (DESIGN §40.54 items 8.2, 13 and 14, ruling R14):
stale drafts and staged restores planted here with old times, previewed and
removed a page of conversations at a time, their freed bytes computed from
the size rule, the cursor as a place that confers nothing, and the route as
the operator's alone."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.contract.file.test_records import record_line
from tests.contract.records.reading import read
from tests.contract.records.searching import (
    ABSENT,
    SWEEP,
    UNGRANTED,
    WHOLE_STORE,
    cursor,
    forbidden,
    message,
    post,
    raw,
    refusal,
    store_lock,
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
from tests.contract.support import ADMIN, LIMITED, Binary, Server, serve

OLD = 946_684_800
LIMIT = "sweep limit must be in 1..100"
CURSOR = "sweep cursor must be 1 to 4096 bytes"
GRAMMAR = "must match [A-Za-z0-9_.-]{1,128} \u2014 one flat segment"
NOTHING: dict[str, Any] = {"conversations": [], "next_cursor": None, "write": False}
GRANTS = """grant_format = 1
[[clients]]
actor = "client:contract"
operator = true
[[clients]]
actor = "client:reader"
[[clients.conversations]]
prefix = "w-"
read = true
append = true
"""


def torn(state: Path) -> None:
    """A state cut short counts from its file's time (item 9 "Opening")."""
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_bytes(b"{")
    os.utime(state, (OLD, OLD))


def stale_draft(home: Path, conversation: str, draft: str, *texts: str) -> int:
    """A stale open draft holding a prompt for each text; its bytes by the
    size rule: bookkeeping and records."""
    drafts = home / "conversations" / conversation / ".drafts"
    torn(drafts / f"{draft}.state")
    items = [
        record(index, index, "user", "prompt", text) for index, text in enumerate(texts)
    ]
    lines = "".join(record_line(item) for item in items)
    (drafts / f"{draft}.jsonl").write_text(lines, encoding="utf-8")
    held = sum(record_bytes(conversation, item) for item in items)
    return bookkeeping(conversation, draft) + held


def stale_restore(home: Path, conversation: str, restore: str) -> int:
    """A stale staged restore that got no further than its bookkeeping."""
    torn(home / ".records" / "restore" / conversation / restore / "state")
    return bookkeeping(conversation, restore)


def swept(conversation: str, drafts: int, restores: int, freed: int) -> dict[str, Any]:
    return {
        "conversation_id": conversation,
        "drafts": drafts,
        "restores": restores,
        "freed_bytes": freed,
    }


def aged(home: Path, conversation: str, draft: str) -> None:
    """Date a draft the wire opened to the year 2000, in its own state."""
    state = home / "conversations" / conversation / ".drafts" / f"{draft}.state"
    text = state.read_text(encoding="utf-8")
    old, count = re.subn(
        r'"touched_at":"[^"]*"', '"touched_at":"2000-01-01T00:00:00Z"', text
    )
    assert count == 1
    state.write_text(old, encoding="utf-8")


def test_a_preview_answers_what_the_write_then_removes(enabled: Binary) -> None:
    home = enabled.home
    b = stale_restore(home, "w-b", "backup")
    c = (
        stale_draft(home, "w-c", "one", "a", "bc")
        + stale_draft(home, "w-c", "two")
        + stale_restore(home, "w-c", "r1")
    )
    with serve(enabled) as server:
        # What is not stale: a fresh draft beside a stale one, a fresh
        # draft alone, a sealed turn, and a draft opened and aged below.
        write(server, "w-a", "fresh", prompts(2))
        # Planted after that opening, which cleans its own conversation.
        a = stale_draft(home, "w-a", "old", "hé")
        # Literal bytes of item 14: `o + 2c + 1024` and `u(text) + 2c + 256`.
        assert (a, b, c) == (
            (3 + 6 + 1024) + (3 + 6 + 256),
            6 + 6 + 1024,
            (3 + 6 + 1024)
            + (1 + 6 + 256)
            + (2 + 6 + 256)
            + (3 + 6 + 1024)
            + (2 + 6 + 1024),
        )
        write(server, "w-d", "fresh", prompts(1))
        write(server, "w-e", "done", prompts(3), seal=True)
        write(server, "w-f", "real", [record(0, 0, "user", "prompt", "alpha open")])
        aged(home, "w-f", "real")
        f = bookkeeping("w-f", "real") + (10 + 6 + 256)
        assert f == 1_306
        expected = [
            swept("w-a", 1, 0, a),
            swept("w-b", 0, 1, b),
            swept("w-c", 2, 1, c),
            swept("w-f", 1, 0, f),
        ]
        preview = sweep(server)
        assert preview == {
            "conversations": expected,
            "next_cursor": None,
            "write": False,
        }
        assert sweep(server, write=False, limit=50) == preview
        assert raw(server, SWEEP, {}) == (200, compact(preview))
        # A preview removed nothing.
        assert (home / "conversations" / "w-a" / ".drafts" / "old.jsonl").is_file()
        assert (home / ".records" / "restore" / "w-b" / "backup" / "state").is_file()
        assert (home / "conversations" / "w-f" / ".drafts" / "real.jsonl").is_file()
        removed = sweep(server, write=True)
        assert removed == {**preview, "write": True}
        assert raw(server, SWEEP, {"write": True}) == (
            200,
            compact({"conversations": [], "next_cursor": None, "write": True}),
        )
        assert sweep(server) == NOTHING
        for conversation in ["w-a", "w-c", "w-f"]:
            drafts = home / "conversations" / conversation / ".drafts"
            left = sorted(path.name for path in drafts.iterdir())
            assert left == (
                ["fresh.jsonl", "fresh.state"] if conversation == "w-a" else []
            )
        assert not list((home / ".records" / "restore").glob("*/*"))
        # The swept draft is gone, the fresh ones and the sealed turn stay.
        assert probe(server, "w-f", "real")["state"] == "none"
        assert probe(server, "w-a", "fresh")["next_seq"] == 2
        assert probe(server, "w-d", "fresh")["next_seq"] == 1
        assert len(read(server, "w-e")["records"]) == 3
        assert server.post("store/conversations") == {"conversations": ["w-e"]}


def test_a_probe_is_not_a_touch_and_a_repeat_is(enabled: Binary) -> None:
    """Ruling R19: `records: []` without `seal` or `discard` changes
    nothing, the draft's time included, so a stale draft that is probed is
    still swept. A request carrying records touches, a pure repeat too."""
    home = enabled.home
    one = [record(0, 0, "user", "prompt", "alpha open")]
    with serve(enabled) as server:
        for conversation in ["w-probed", "w-repeated"]:
            write(server, conversation, "real", one)
            aged(home, conversation, "real")
        state = home / "conversations" / "w-probed" / ".drafts" / "real.state"
        before = state.read_bytes()
        freed = bookkeeping("w-probed", "real") + record_bytes("w-probed", one[0])
        probed = probe(server, "w-probed", "real")
        assert (probed["state"], probed["next_seq"]) == ("open", 1)
        assert (probed["stored_bytes"], probed["freed_bytes"]) == (0, 0)
        assert state.read_bytes() == before
        repeated = write(server, "w-repeated", "real", one)
        assert (repeated["next_seq"], repeated["stored_bytes"]) == (1, 0)
        rows = [swept("w-probed", 1, 0, freed)]
        assert sweep(server)["conversations"] == rows
        assert sweep(server, write=True)["conversations"] == rows
        assert probe(server, "w-probed", "real")["state"] == "none"
        assert probe(server, "w-repeated", "real")["next_seq"] == 1


def test_limit_counts_conversations_in_bytewise_order_across_pages(
    enabled: Binary,
) -> None:
    home = enabled.home
    ids = ["Zed", "_x", "c-0", "c-1", "c.0"]
    assert ids == sorted(ids, key=str.encode)
    freed = {name: stale_restore(home, name, "r") for name in reversed(ids)}
    rows = [swept(name, 0, 1, freed[name]) for name in ids]
    with serve(enabled) as server:
        first = sweep(server, limit=2)
        assert first == {
            "conversations": rows[:2],
            "next_cursor": cursor("sweep-v1", "_x"),
            "write": False,
        }
        assert first["next_cursor"] == "WyJzd2VlcC12MSIsIl94Il0="
        second = sweep(server, limit=2, cursor=first["next_cursor"])
        assert second["conversations"] == rows[2:4]
        assert second["next_cursor"] == cursor("sweep-v1", "c-1")
        third = sweep(server, limit=2, cursor=second["next_cursor"])
        assert third == {
            "conversations": rows[4:],
            "next_cursor": None,
            "write": False,
        }
        # A page that ends with the store's last conversation has no cursor.
        assert sweep(server, limit=5) == {
            "conversations": rows,
            "next_cursor": None,
            "write": False,
        }
        assert sweep(server, limit=4)["next_cursor"] == cursor("sweep-v1", "c-1")
        assert sweep(server, limit=1)["conversations"] == rows[:1]
        assert sweep(server, limit=100)["conversations"] == rows
        # Removing page by page: each call takes the next conversations.
        gone = sweep(server, limit=2, write=True)
        assert gone == {**first, "write": True}
        rest = sweep(server, limit=2, write=True, cursor=gone["next_cursor"])
        assert rest == {**second, "write": True}
        assert sweep(server)["conversations"] == rows[4:]


def test_the_default_limit_is_fifty_conversations(enabled: Binary) -> None:
    names = [f"c-{number:02}" for number in range(51)]
    for name in reversed(names):
        stale_restore(enabled.home, name, "backup")
    with serve(enabled) as server:
        page = sweep(server)
        assert [one["conversation_id"] for one in page["conversations"]] == names[:50]
        assert page["next_cursor"] == cursor("sweep-v1", "c-49")
        assert all(one["freed_bytes"] == 1_038 for one in page["conversations"])
        assert len(sweep(server, limit=100)["conversations"]) == 51
        last = sweep(server, cursor=page["next_cursor"], write=True)
        assert last == {
            "conversations": [swept("c-50", 0, 1, 1_038)],
            "next_cursor": None,
            "write": True,
        }
        assert len(sweep(server, limit=100)["conversations"]) == 50


def test_a_cursor_names_a_place_and_confers_nothing(enabled: Binary) -> None:
    """Ruling R14: the cursor is the last conversation visited and nothing
    else. A preview's cursor sent with `write: true`, and one made up,
    sweep the conversations after the id they name and no other."""
    home = enabled.home
    names = ["c-0", "c-1", "c-2", "c-3", "c-4"]
    for name in names:
        stale_restore(home, name, "r")

    def left() -> list[str]:
        restores = home / ".records" / "restore"
        return sorted(path.name for path in restores.iterdir() if any(path.iterdir()))

    with serve(enabled) as server:
        preview = sweep(server, limit=3)
        token = preview["next_cursor"]
        assert token == cursor("sweep-v1", "c-2")
        # The preview's cursor with another limit and with `write: true`.
        taken = sweep(server, limit=1, write=True, cursor=token)
        assert [one["conversation_id"] for one in taken["conversations"]] == ["c-3"]
        assert taken["next_cursor"] == cursor("sweep-v1", "c-3")
        assert left() == ["c-0", "c-1", "c-2", "c-4"]
        # Made up, naming a conversation the store does not hold.
        after = sweep(server, write=True, cursor=cursor("sweep-v1", "c-1z"))
        assert [one["conversation_id"] for one in after["conversations"]] == [
            "c-2",
            "c-4",
        ]
        assert after["next_cursor"] is None
        assert left() == ["c-0", "c-1"]
        # Made up, naming the last id or one past every id: nothing.
        for last in ["c-1", "zzz"]:
            assert sweep(server, write=True, cursor=cursor("sweep-v1", last)) == {
                "conversations": [],
                "next_cursor": None,
                "write": True,
            }
        assert left() == ["c-0", "c-1"]
        assert len(sweep(server)["conversations"]) == 2


def test_a_cursor_of_another_method_or_shape_is_refused(enabled: Binary) -> None:
    stale_restore(enabled.home, "c-0", "r")
    stale_restore(enabled.home, "c-1", "r")
    with serve(enabled) as server:
        write(server, "s-read", "d", prompts(3), seal=True)
        page = read(server, "s-read", page_bytes=1)
        continuation = page["continuation"]
        assert isinstance(continuation, str)
        foreign = [
            cursor("sweep-v2", "c-0"),
            cursor("sweep", "c-0"),
            cursor("list", "c-0"),
            cursor("search_turns", "c-0"),
            continuation,
            cursor("sweep-v1"),
            cursor("sweep-v1", "c-0", "c-1"),
            cursor("sweep-v1", 7),
            cursor("sweep-v1", None),
            cursor("c-0"),
            "c-0",
            "not base64!",
            "WyJzd2VlcC12MSIsImMtMCJd" + "A",
        ]
        for token in foreign:
            for write_too in [False, True]:
                body = {"cursor": token, "write": write_too}
                said = message(server, SWEEP, body)
                assert said == f"malformed sweep-v1 cursor: {token!r}", token
        for token in ["", "x" * 4_097, "é" * 2_049]:
            assert message(server, SWEEP, {"cursor": token, "write": True}) == CURSOR
        # An id outside the grammar inside a well-formed cursor.
        for bad, why in [
            ("has space", GRAMMAR),
            ("a/b", GRAMMAR),
            ("", GRAMMAR),
            ("..", "id '..' is reserved"),
        ]:
            body = {"cursor": cursor("sweep-v1", bad), "write": True}
            assert message(server, SWEEP, body) == why
        # No refused call removed anything.
        assert len(sweep(server)["conversations"]) == 2
        assert sweep(server, cursor=cursor("sweep-v1", "c-0"))["conversations"] == [
            swept("c-1", 0, 1, 1 + 6 + 1024)
        ]


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"limit": 0}, LIMIT),
        ({"limit": 101}, LIMIT),
        ({"limit": 1 << 63}, LIMIT),
        ({"limit": 0, "write": True}, LIMIT),
        ({"limit": None}, "parameter 'limit' must be a non-negative integer"),
        ({"limit": "5"}, "parameter 'limit' must be a non-negative integer"),
        ({"limit": 2.5}, "parameter 'limit' must be a non-negative integer"),
        ({"limit": -1}, "parameter 'limit' must be a non-negative integer"),
        ({"limit": True}, "parameter 'limit' must be a non-negative integer"),
        ({"cursor": None}, "parameter 'cursor' must be a string"),
        ({"cursor": 7}, "parameter 'cursor' must be a string"),
        ({"cursor": ["c-0"]}, "parameter 'cursor' must be a string"),
        ({"write": None}, "parameter 'write' must be a boolean"),
        ({"write": 1}, "parameter 'write' must be a boolean"),
        ({"write": "true"}, "parameter 'write' must be a boolean"),
        ({"discard": True}, "unknown parameter: 'discard'"),
        ({"conversation_id": "c-0"}, "unknown parameter: 'conversation_id'"),
        ({"conversations": ["c-0"]}, "unknown parameter: 'conversations'"),
        ({"write": True, "seconds": 0}, "unknown parameter: 'seconds'"),
        ({"Write": True}, "unknown parameter: 'Write'"),
        ({"next_cursor": None}, "unknown parameter: 'next_cursor'"),
    ],
)
def test_each_rule_of_the_table_refuses_and_removes_nothing(
    enabled: Binary, body: dict[str, Any], why: str
) -> None:
    freed = stale_restore(enabled.home, "c-0", "r")
    with serve(enabled) as server:
        assert message(server, SWEEP, body) == why
        assert sweep(server)["conversations"] == [swept("c-0", 0, 1, freed)]


def test_a_store_without_records_answers_its_own_refusal(server: Server) -> None:
    for body in [{}, {"write": True}, {"limit": 0, "nonsense": 1}]:
        assert refusal(server, SWEEP, body, 400, "agent_configuration_error") == ABSENT
    assert forbidden(server, SWEEP, {}, LIMITED) == WHOLE_STORE


def test_the_sweep_is_the_operators_alone(enabled: Binary, tmp_path: Path) -> None:
    """A whole-store route: a constrained token is refused whatever its
    body names, and under grants only an operator sweeps. A refusal
    answers while the store's lock is held, and removes nothing."""
    home = enabled.home
    freed = stale_draft(home, "allowed-1", "old", "mine")
    rows = [swept("allowed-1", 1, 0, freed)]
    bodies: list[dict[str, Any]] = [
        {},
        {"write": True},
        {"limit": 1, "write": True},
        {"conversation_id": "allowed-1", "write": True},
        {"conversations": ["allowed-1"], "write": True},
        {"conversations": [], "write": True},
        {"scope": "user:allowed", "write": True},
        {"cursor": cursor("sweep-v1", "allowed-0"), "write": True},
    ]
    with serve(enabled) as server:
        for body in bodies:
            assert forbidden(server, SWEEP, body, LIMITED) == WHOLE_STORE, body
        with store_lock(home):
            assert forbidden(server, SWEEP, {"write": True}, LIMITED) == WHOLE_STORE
            answers: list[Any] = []
            waiting = threading.Thread(
                target=lambda: answers.append(post(server, SWEEP, {}))
            )
            waiting.start()
            waiting.join(0.5)
            assert waiting.is_alive() and answers == []
        waiting.join(10)
        assert answers[0].status == 200
        assert answers[0].body["conversations"] == rows
    grants = tmp_path / "grants.toml"
    grants.write_text(GRANTS, encoding="utf-8")
    env = {
        "NEOSIAN_SERVE_GRANTS": str(grants),
        "NEOSIAN_SERVE_TOKEN": (
            f"client:contract={ADMIN},client:reader=reader,client:stranger=stranger,"
            "client:fenced+allowed-=fenced"
        ),
    }
    grants.write_text(
        GRANTS + '[[clients]]\nactor = "client:fenced"\noperator = true\n',
        encoding="utf-8",
    )
    with serve(enabled, extra_env=env) as server:
        with store_lock(home):
            for token in ["reader", "stranger"]:
                for body in bodies:
                    assert forbidden(server, SWEEP, body, token) == UNGRANTED
            # An operator's grant does not widen a constrained token.
            for body in bodies:
                assert forbidden(server, SWEEP, body, "fenced") == WHOLE_STORE
        assert sweep(server)["conversations"] == rows
        assert sweep(server, write=True)["conversations"] == rows
        assert sweep(server) == NOTHING


def test_an_answer_is_within_its_published_bound(enabled: Binary) -> None:
    """Item 14: an answer is at most `limit * 2,048 + 4,160` bytes.
    Measured with the longest ids there are, 128 bytes, a hundred to a
    page and a cursor naming one of them."""
    names = [f"{number:03}" + "x" * 125 for number in range(101)]
    freed = 0
    for name in names:
        freed = stale_restore(enabled.home, name, "r" * 64)
    assert freed == 64 + 256 + 1024
    with serve(enabled) as server:
        for limit in [1, 50, 100]:
            status, answer = raw(server, SWEEP, {"limit": limit})
            assert status == 200
            assert len(answer) <= limit * 2_048 + 4_160
            page = sweep(server, limit=limit)
            assert compact(page) == answer
            assert len(page["conversations"]) == limit
            assert page["next_cursor"] == cursor("sweep-v1", names[limit - 1])
            assert len(page["next_cursor"]) == 192
            # An entry is 193 bytes here, the cursor 192 and the rest 51.
            assert len(answer) == limit * 193 + (limit - 1) + 243
