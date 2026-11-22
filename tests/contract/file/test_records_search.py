"""History search over turns stored as records (DESIGN §40.54, T1b2a1).

A records home written here by hand: whole turns 1 and 3, a 70,000-byte
records turn 2 and a short records turn 4, each holding "alpha". Under a
65,536-byte NEOSIAN_MAX_TURN_BYTES turn 2 is too long to answer whole: it is
counted in `omitted`, the answer's last key, and size pages neither repeat
nor lose a turn around it.
"""

import json
from pathlib import Path
from typing import Any

from tests.contract.file.test_records import record, record_line
from tests.contract.support import Binary, serve

SESSION = "c-search"
LIMIT = {"NEOSIAN_MAX_TURN_BYTES": "65536"}


def whole(turn: int, minute: int, text: str) -> dict[str, Any]:
    return {
        "neosian_format": 1,
        "turn": turn,
        "created_at": f"2026-11-21T10:0{minute}:00Z",
        "messages": [
            {
                "role": "user",
                "content": text,
                "reasoning": None,
                "tool_calls": [],
                "tool_call_id": None,
            }
        ],
        "actor": None,
    }


def header(turn: int, minute: int, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "neosian_format": 2,
        "turn": turn,
        "created_at": f"2026-11-21T10:0{minute}:00Z",
        "actor": None,
        "records": len(records),
        "bytes": sum(len(item["text"].encode()) for item in records),
        "draft": f"d{turn}",
    }


def plant(home: Path, *, marker: bool = True) -> None:
    long = "alpha " + "x" * 69_994
    turns = {
        2: [
            record(0, 0, "user", "prompt", long[:65_534], size=70_000),
            record(
                1,
                0,
                "user",
                "prompt",
                long[65_534:],
                content=None,
                chunk=1,
                size=70_000,
            ),
        ],
        4: [record(0, 0, "user", "prompt", "alpha four")],
    }
    conversation = home / "conversations" / SESSION
    (conversation / "records").mkdir(parents=True)
    rows = [
        whole(1, 0, "alpha one"),
        header(2, 1, turns[2]),
        whole(3, 2, "alpha three"),
        header(4, 3, turns[4]),
    ]
    log = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    (conversation / "turns.jsonl").write_text(log, encoding="utf-8")
    for turn, items in turns.items():
        text = "".join(record_line(item) for item in items)
        (conversation / "records" / f"{turn}.jsonl").write_text(text, "utf-8")
    if marker:
        (home / ".records").mkdir()
        (home / ".records" / "format").write_text('{"neosian_records":1}\n')


def search(server: Any, **body: Any) -> Any:
    reply = server.request("/v1/conversation/search_turns", {"query": "alpha", **body})
    assert reply.status == 200, reply
    return reply.body


def numbers(body: Any) -> list[int]:
    return [turn["turn"] for turn in body["turns"]]


def test_a_turn_too_long_to_answer_whole_is_counted_in_omitted(binary: Binary) -> None:
    plant(binary.home)
    with serve(binary, extra_env=LIMIT) as server:
        first = search(server)
        assert (numbers(first), list(first), first["omitted"]) == (
            [4, 3, 1],
            ["turns", "omitted"],
            1,
        )
        newest = search(server, order="newest")
        assert list(newest) == ["turns", "order", "omitted"]
        assert numbers(newest) == [4, 3, 1]
        ranked = search(server, order="relevance")
        assert list(ranked) == ["turns", "order", "scores", "omitted"]
        assert sorted(numbers(ranked)) == [1, 3, 4] and len(ranked["scores"]) == 3
    with serve(binary) as server:
        answered = search(server)
        assert (numbers(answered), list(answered)) == ([4, 3, 2, 1], ["turns"])


def pages(server: Any, **body: Any) -> list[Any]:
    found = [search(server, page_bytes=1, **body)]
    while found[-1]["continuation"] is not None:
        token = found[-1]["continuation"]
        found.append(search(server, page_bytes=1, continuation=token, **body))
        assert len(found) < 20
    return found


def test_size_pages_neither_repeat_nor_lose_a_turn_around_an_omitted_one(
    binary: Binary,
) -> None:
    plant(binary.home)
    with serve(binary, extra_env=LIMIT) as server:
        plain = pages(server)
        assert [numbers(page) for page in plain] == [[4], [3], [1]]
        assert [page.get("omitted", 0) for page in plain] == [0, 1, 0]
        assert list(plain[1]) == ["turns", "continuation", "omitted"]
        for order in ["newest", "relevance"]:
            paged = pages(server, order=order)
            turns = [turn for page in paged for turn in numbers(page)]
            assert sorted(turns) == [1, 3, 4], order
            assert sum(page.get("omitted", 0) for page in paged) == 1, order
            for page in paged:
                assert page["turns"] or page["continuation"] is None, order


def test_without_the_marker_a_search_answers_as_before(binary: Binary) -> None:
    plant(binary.home, marker=False)
    with serve(binary) as server:
        body = search(server)
        assert (numbers(body), list(body)) == ([3, 1], ["turns"])
