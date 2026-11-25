"""`conversation/read_records` answers as bytes (DESIGN §40.54 items 3.1,
3.6, 8.2 and 10): `turns`, then `records`, then `continuation` in the raw
answer although a store reads each turn's header with its records, the
fifteen keys of a record and the six of a header in their order, an answer
from the spool file equal to the same bytes, and item 10's example on a
home planted by hand with the design's own turn number, time and actor."""

from __future__ import annotations

import json
from typing import Any

from tests.contract.file.test_records import record_line
from tests.contract.records.example import (
    EXAMPLE,
    LINED,
    LINED_CHUNKS,
    LINED_EXAMPLE,
    LINED_MESSAGES,
    MESSAGES,
    SESSION,
)
from tests.contract.records.reading import (
    all_answered,
    answered,
    cost,
    header,
    joined,
    page_bytes,
    pages,
    raw,
    read,
)
from tests.contract.records.support import compact, record, write
from tests.contract.support import Binary, Server, serve

STAMP = "2026-11-21T14:03:07.512000Z"
ACTOR = "client:default/claude-code:7f3a"
HEADER = {
    "turn": 42,
    "created_at": STAMP,
    "actor": ACTOR,
    "redacted": False,
    "records": 16,
    "bytes": 812_400,
}


def test_turns_precede_records_in_the_raw_answer(records: Server) -> None:
    """Three turns, each header read with its records: the answer's bytes
    still hold every header before the first record."""
    one = [
        record(0, 0, "user", "prompt", "héllo\n"),
        record(1, 1, "tool", "tool_result", ""),
    ]
    one[1]["call_id"] = "t1"
    for draft in ["a", "b", "c"]:
        write(records, "s-raw", draft, one, seal=True, actor="me")
    stored = records.post("conversation/read_turns", conversation_id="s-raw")["turns"]
    stamps = [turn["created_at"] for turn in stored]
    status, answer = raw(records, {"conversation_id": "s-raw"})
    assert status == 200

    def turn(number: int) -> bytes:
        stamp = stamps[number - 1].encode()
        return (
            b'{"turn":%d,"created_at":"%s","actor":"client:contract/me",'
            b'"redacted":false,"records":2,"bytes":7}' % (number, stamp)
        )

    def pair(number: int) -> bytes:
        return (
            b'{"turn":%d,"seq":0,"message":0,"role":"user","kind":"prompt",'
            b'"content":"text","block":null,"call":null,"call_id":null,"name":null,'
            b'"chunk":0,"size":7,"text":"h\xc3\xa9llo\\n","tail":null,"truncated":false},'
            b'{"turn":%d,"seq":1,"message":1,"role":"tool","kind":"tool_result",'
            b'"content":"text","block":null,"call":null,"call_id":"t1","name":null,'
            b'"chunk":0,"size":0,"text":"","tail":null,"truncated":false}'
            % (number, number)
        )

    assert answer == (
        b'{"turns":['
        + b",".join(turn(number) for number in [1, 2, 3])
        + b'],"records":['
        + b",".join(pair(number) for number in [1, 2, 3])
        + b'],"continuation":null}'
    )
    assert answer.index(b'"turns":[') < answer.index(b'"records":[')
    assert answer.index(b'"records":[') < answer.index(b'"continuation":')
    assert answer.rindex(b'"created_at"') < answer.index(b'"seq"')
    # A page with a continuation ends on it, and an empty one keeps the keys.
    status, first = raw(records, {"conversation_id": "s-raw", "page_bytes": 1})
    token = json.loads(first)["continuation"]
    assert first == (
        b'{"turns":['
        + turn(1)
        + b'],"records":['
        + pair(1).split(b"},{")[0]
        + b'}],"continuation":"'
        + token.encode()
        + b'"}'
    )
    assert raw(records, {"conversation_id": "s-raw", "after": 3}) == (
        200,
        b'{"turns":[],"records":[],"continuation":null}',
    )
    only = raw(records, {"conversation_id": "s-raw", "kinds": [], "turn": 2})
    assert only == (
        200,
        b'{"turns":[' + turn(2) + b'],"records":[],"continuation":null}',
    )
    # A head keeps the same fifteen keys in the same order.
    write(
        records, "s-raw", "d", [record(0, 0, "user", "prompt", "abcdefgh")], seal=True
    )
    head = raw(
        records,
        {
            "conversation_id": "s-raw",
            "turn": 4,
            "heads": {"prompt": 4},
            "kinds": ["prompt"],
        },
    )[1]
    assert head.endswith(
        b'"records":[{"turn":4,"seq":0,"message":0,"role":"user","kind":"prompt",'
        b'"content":"text","block":null,"call":null,"call_id":null,"name":null,'
        b'"chunk":0,"size":8,"text":"ab","tail":"gh","truncated":true}],'
        b'"continuation":null}'
    )


def test_an_answer_past_1_mib_is_the_same_bytes_from_the_spool(enabled: Binary) -> None:
    """Two turns of item 3.8's example under a page of 8 MiB: 1.6 MiB of
    records behind two headers, sent from the server's spool with its
    length, byte for byte the page assembled here."""
    with serve(enabled) as server:
        for draft in ["a", "b"]:
            write(server, "s-big", draft, EXAMPLE[:9])
            write(server, "s-big", draft, EXAMPLE[9:], seal=True)
        stored = server.post(
            "conversation/read_turns", conversation_id="s-big", page_bytes=1 << 23
        )["turns"]
        assert [turn["messages"] for turn in stored] == [MESSAGES, MESSAGES]
        headers = [header(turn, 16, 812_400) for turn in stored]
        items = all_answered(1, EXAMPLE) + all_answered(2, EXAMPLE)
        whole = page_bytes(headers, items, None)
        assert len(whole) > 1_600_000
        body = {"conversation_id": "s-big", "page_bytes": 1 << 23}
        assert raw(server, body) == (200, whole)
        # At the default the first page stops inside the second turn, under 1 MiB.
        status, first = raw(server, {"conversation_id": "s-big"})
        page = json.loads(first)
        assert status == 200 and len(first) <= 983_040 + 4_160
        assert page["turns"] == headers
        assert page["records"] == items[: len(page["records"])]
        assert 16 < len(page["records"]) < 32
        rest = read(server, "s-big", continuation=page["continuation"])
        assert rest["turns"] == headers[1:]
        assert page["records"] + rest["records"] == items


def test_a_page_past_the_temporary_budget_is_refused_and_smaller_pages_answer(
    enabled: Binary,
) -> None:
    """A spool's refusal is the ordinary `value_error` naming its setting,
    found after the store was read; the default page needs no file."""
    with serve(enabled, extra_env={"NEOSIAN_MAX_TEMP_BYTES": "1100000"}) as server:
        for draft in ["a", "b"]:
            write(server, "s-big", draft, EXAMPLE[:9])
            write(server, "s-big", draft, EXAMPLE[9:], seal=True)
        status, refused = raw(
            server, {"conversation_id": "s-big", "page_bytes": 1 << 23}
        )
        error = json.loads(refused)["error"]
        assert (status, error["code"], error["details"]) == (400, "value_error", {})
        assert error["message"].startswith(
            "resource limit NEOSIAN_MAX_TEMP_BYTES exceeded: "
        )
        assert error["message"].endswith(
            " > 1100000; select a smaller batch or raise this setting"
        )
        paged = pages(server, "s-big", None)
        assert len(paged) == 2
        assert joined(paged)[1] == all_answered(1, EXAMPLE) + all_answered(2, EXAMPLE)


def plant(binary: Binary) -> None:
    """Forty-one short turns stored whole, then item 3.8's turn as records
    under the header of items 9 and 10: the turn log's format-2 row and the
    turn's records file, each line with the SHA-256 of its record."""
    conversation = binary.home / "conversations" / SESSION
    (conversation / "records").mkdir(parents=True)
    rows: list[dict[str, Any]] = [
        {
            "neosian_format": 1,
            "turn": number,
            "created_at": "2026-11-21T14:00:00Z",
            "messages": [
                {
                    "role": "user",
                    "content": f"earlier {number}",
                    "reasoning": None,
                    "tool_calls": [],
                    "tool_call_id": None,
                }
            ],
            "actor": "cli:t",
        }
        for number in range(1, 42)
    ]
    rows.append(
        {
            "neosian_format": 2,
            "turn": 42,
            "created_at": STAMP,
            "actor": ACTOR,
            "records": 16,
            "bytes": 812_400,
            "draft": "d7c1e0b2a9f44e57",
        }
    )
    log = "".join(compact(row).decode() + "\n" for row in rows)
    (conversation / "turns.jsonl").write_text(log, encoding="utf-8")
    lines = "".join(record_line(item) for item in LINED_EXAMPLE)
    (conversation / "records" / "42.jsonl").write_text(lines, encoding="utf-8")


def test_the_design_example_the_light_view_then_the_result_whole(
    enabled: Binary,
) -> None:
    """Item 10's example, literally: turn 42 of `cc-7f3a` under tool heads
    of 200 bytes, then the tool result fetched whole in pages of 262,144,
    which hold records 2, 3 and 4 and a continuation."""
    plant(enabled)
    # Two hundred bytes hold 198 within the quotes, and a line feed costs
    # two of them: a line, its feed and 67 bytes; 91 bytes, a feed and the
    # last 105.
    head = "a" * 129 + "\n" + "a" * 67
    tail = "a" * 91 + "\n" + "a" * 105
    assert LINED.startswith(head) and LINED.endswith(tail)
    assert (len(LINED), len(LINED_CHUNKS), len(LINED_CHUNKS[12])) == (
        812_345,
        13,
        32_345,
    )
    with serve(enabled) as server:
        light = raw(
            server,
            {
                "conversation_id": "cc-7f3a",
                "turn": 42,
                "kinds": ["prompt", "reply", "tool_call", "tool_result"],
                "heads": {"tool_result": 200},
            },
        )
        prefix = b'{"turn":42,"created_at":"2026-11-21T14:03:07.512000Z",'
        assert light == (
            200,
            b'{"turns":['
            + prefix
            + b'"actor":"client:default/claude-code:7f3a","redacted":false,'
            b'"records":16,"bytes":812400}],"records":['
            b'{"turn":42,"seq":0,"message":0,"role":"user","kind":"prompt",'
            b'"content":"text","block":null,"call":null,"call_id":null,"name":null,'
            b'"chunk":0,"size":13,"text":"run the tests","tail":null,"truncated":false},'
            b'{"turn":42,"seq":1,"message":1,"role":"assistant","kind":"tool_call",'
            b'"content":"none","block":null,"call":0,"call_id":"toolu_01","name":"Bash",'
            b'"chunk":0,"size":23,"text":"{\\"command\\":\\"make test\\"}",'
            b'"tail":null,"truncated":false},'
            b'{"turn":42,"seq":2,"message":2,"role":"tool","kind":"tool_result",'
            b'"content":"text","block":null,"call":null,"call_id":"toolu_01","name":null,'
            b'"chunk":0,"size":812345,"text":"'
            + b"a" * 129
            + b"\\n"
            + b"a" * 67
            + b'","tail":"'
            + b"a" * 91
            + b"\\n"
            + b"a" * 105
            + b'","truncated":true},'
            b'{"turn":42,"seq":15,"message":3,"role":"assistant","kind":"reply",'
            b'"content":"text","block":null,"call":null,"call_id":null,"name":null,'
            b'"chunk":0,"size":19,"text":"All 412 tests pass.","tail":null,'
            b'"truncated":false}],"continuation":null}',
        )
        # The result whole, as the example has it: three records a page. A
        # chunk of 65,000 bytes with 500 line feeds encodes to 65,502 bytes
        # and costs 65,706 as a record, or 65,704 after the first, whose
        # `content` is null. With the header's 142, three are 197,256
        # bytes, within 262,144, and a fourth would make 262,960.
        chunks = all_answered(42, LINED_EXAMPLE[2:15])
        assert [cost(HEADER), cost(chunks[0]), cost(chunks[1])] == [142, 65_706, 65_704]
        assert 142 + 65_706 + 2 * 65_704 == 197_256 <= 262_144 < 197_256 + 65_704
        fetch: dict[str, Any] = {"turn": 42, "seq": 2, "piece": True}
        first = read(server, "cc-7f3a", **fetch, page_bytes=262_144)
        assert first["turns"] == [HEADER]
        assert first["records"] == chunks[:3]
        assert [item["seq"] for item in first["records"]] == [2, 3, 4]
        token = first["continuation"]
        assert isinstance(token, str) and token.startswith("WyJyZWNvcmRzLXYxL3JlYWQi")
        seen = list(first["records"])
        counts = [3]
        while token is not None:
            page = read(
                server, "cc-7f3a", **fetch, page_bytes=262_144, continuation=token
            )
            assert page["turns"] == [HEADER]
            seen += page["records"]
            counts.append(len(page["records"]))
            token = page["continuation"]
        # The last page holds the short thirteenth chunk with three whole ones.
        assert counts == [3, 3, 3, 4]
        assert seen == chunks
        assert "".join(item["text"] for item in seen) == LINED
        assert [item["text"] for item in seen] == LINED_CHUNKS
        # The split rule cuts the same text into the same chunks.
        whole = server.post(
            "conversation/append_turn",
            conversation_id="s-lined",
            messages=LINED_MESSAGES,
        )
        converted = read(server, "s-lined", page_bytes=1 << 23)
        assert converted["records"] == all_answered(1, LINED_EXAMPLE)
        assert converted["turns"] == [header(whole["turn"], 16, 812_400)]
        # The earlier turns, stored whole, read through the same route.
        earlier = read(server, "cc-7f3a", through=41, kinds=["prompt"])
        assert [item["text"] for item in earlier["records"]] == [
            f"earlier {number}" for number in range(1, 42)
        ]
        assert earlier["turns"][40] == {
            "turn": 41,
            "created_at": "2026-11-21T14:00:00Z",
            "actor": "cli:t",
            "redacted": False,
            "records": 1,
            "bytes": 10,
        }
        assert answered(42, LINED_EXAMPLE[15]) == json.loads(light[1])["records"][3]
