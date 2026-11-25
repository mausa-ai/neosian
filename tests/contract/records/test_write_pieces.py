"""`write_records` and what a piece may hold (DESIGN §40.54 items 3.2, 3.3
and 9 "Checks" and "Opening"): each JSON-piece check with the message it
refuses with, one record of every kind read back whole, and stale drafts
discarded at an opening. The stale drafts are planted by hand."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.contract.file.test_records import record_line
from tests.contract.records.support import (
    bookkeeping,
    conflict,
    nothing,
    probe,
    prompts,
    record,
    record_bytes,
    records_bytes,
    refused,
    write,
)
from tests.contract.support import Binary, Server, serve

SESSION = "s-piece"
IMAGE = '{"type":"image","media_type":"image/png","data":"QUJD","url":null}'


def call(text: str, seq: int = 0, **more: Any) -> dict[str, Any]:
    item = record(seq, 0, "assistant", "tool_call", text, content="none", call=0)
    return {**item, "call_id": "c1", "name": "Bash", **more}


def piece(kind: str, text: str, **more: Any) -> list[dict[str, Any]]:
    """An assistant reply followed by one piece of `kind` in its message."""
    reply = record(0, 0, "assistant", "reply", "ok")
    return [reply, {**record(1, 0, "assistant", kind, text, content=None), **more}]


def attachment(text: str) -> list[dict[str, Any]]:
    block = record(0, 0, "user", "attachment", text, content="blocks")
    return [{**block, "block": 0}]


def deep(levels: int) -> str:
    """An arguments object whose innermost list is `levels` deep."""
    return '{"a":' + "[" * (levels - 1) + "]" * (levels - 1) + "}"


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (
            [call('{"command": "ls"}')],
            "record 0: not compact JSON: ' ' cannot start a value",
        ),
        ([call('{"command":"ls"} ')], "record 0: not compact JSON: ' ' out of place"),
        ([call('["ls"]')], "record 0: arguments must be an object"),
        ([call('"ls"')], "record 0: arguments must be an object"),
        ([call('{"command":"ls"')], "record 0: not compact JSON: the value ends early"),
        (
            [call('{"n":1.50}')],
            "record 0: not compact JSON: a number not spelled as the codec writes it",
        ),
        (
            [call('{"n":1E2}')],
            "record 0: not compact JSON: a number not spelled as the codec writes it",
        ),
        (
            [call('{"n":-0}')],
            "record 0: not compact JSON: a number not spelled as the codec writes it",
        ),
        (
            [call('{"a":1,"a":2}')],
            "record 0: not compact JSON: a key repeated in its object",
        ),
        (
            [call('{"a":"\\u00e9"}')],
            "record 0: not compact JSON: an escape the codec does not write",
        ),
        (
            [call(deep(123))],
            "record 0: not compact JSON: nested deeper than 122",
        ),
        (
            attachment('{"type":"image","data":"QUJD","media_type":"image/png"}'),
            "record 0: the attachment is not laid out as the codec writes it",
        ),
        (
            attachment(IMAGE.replace("QUJD", "QU JD")),
            "record 0: the attachment is not laid out as the codec writes it",
        ),
        (
            attachment('{"type":"text","text":"a"}'),
            "record 0: an attachment holds a block other than text",
        ),
        (
            attachment('{"type":"video"}'),
            'record 0: Unknown content block type: "video"',
        ),
        (attachment('"image"'), "record 0: the attachment is not a content block"),
        (piece("extra", "{}"), "record 1: the codec leaves an empty extra out"),
        (piece("extra", '""'), "record 1: the codec leaves an empty extra out"),
        (piece("extra", "0"), "record 1: the codec leaves an empty extra out"),
        (piece("extra", "false"), "record 1: the codec leaves an empty extra out"),
        (
            piece("reasoning", "null"),
            "record 1: the codec leaves a null reasoning out",
        ),
        (
            piece("reasoning", "{'a':1}"),
            "record 1: not compact JSON: '\\'' out of place",
        ),
    ],
    ids=lambda value: value[-1]["text"][:40] if isinstance(value, list) else None,
)
def test_a_json_piece_is_exactly_what_the_codec_writes(
    records: Server, items: list[dict[str, Any]], message: str
) -> None:
    body = {"conversation_id": SESSION, "draft": "d", "records": items}
    error = refused(records, body)
    assert (error["message"], error["details"]) == (message, {})
    assert probe(records, SESSION, "d") == nothing("d")


def test_a_piece_is_checked_when_its_last_chunk_lands(records: Server) -> None:
    """A JSON piece split across requests is refused by the chunk that
    breaks it, and the draft keeps what it held."""
    head = call('{"command":', size=19)
    write(records, SESSION, "d", [head])
    tail = call(' "ls"}', 1, content=None, chunk=1, size=19)
    body = {"conversation_id": SESSION, "draft": "d", "records": [tail]}
    assert refused(records, body)["message"] == (
        "record 1: not compact JSON: ' ' cannot start a value"
    )
    assert probe(records, SESSION, "d")["next_seq"] == 1
    whole = call('"ls -l"}', 1, content=None, chunk=1, size=19)
    sealed = write(records, SESSION, "d", [whole], seal=True)
    assert sealed["turn"]["bytes"] == 19
    read = records.post("conversation/read_turns", conversation_id=SESSION)
    assert read["turns"][0]["messages"][0]["tool_calls"] == [
        {"id": "c1", "name": "Bash", "arguments": {"command": "ls -l"}}
    ]


def test_every_kind_of_record_is_stored_and_reads_back_whole(records: Server) -> None:
    """Items 3.2 and 3.3: blocks with an attachment, a call with its extra,
    reasoning and a message's extra, in the order a message gives them."""
    nested = deep(122)
    items = [
        {**record(0, 0, "user", "prompt", "look", content="blocks"), "block": 0},
        {**record(1, 0, "user", "attachment", IMAGE, content=None), "block": 1},
        record(2, 1, "assistant", "reply", "ok"),
        call('{"command":"ls"}', 3, message=1, content=None),
        {**record(4, 1, "assistant", "extra", '{"k":1}', content=None), "call": 0},
        call(nested, 5, message=1, content=None, call=1, call_id="c2", name="Deep"),
        record(6, 1, "assistant", "reasoning", '{"summary":"s"}', content=None),
        record(7, 1, "assistant", "extra", '{"m":true,"x":1.5}', content=None),
        record(8, 2, "system", "system", "be brief"),
        record(9, 3, "tool", "tool_result", "", call_id="c1"),
    ]
    sealed = write(records, SESSION, "kinds", items, seal=True)
    assert sealed["stored_bytes"] == (
        records_bytes(SESSION, items)
        + bookkeeping(SESSION, "kinds")
        + (15 + 5 + 4 * 7 + 512)
        + (7 + 256)
    )
    assert sealed["turn"]["bytes"] == sum(len(item["text"]) for item in items)
    inner: Any = []
    for _ in range(120):
        inner = [inner]
    read = records.post("conversation/read_turns", conversation_id=SESSION)
    assert read["turns"][0]["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "image",
                    "media_type": "image/png",
                    "data": "QUJD",
                    "url": None,
                },
            ],
            "reasoning": None,
            "tool_calls": [],
            "tool_call_id": None,
        },
        {
            "role": "assistant",
            "content": "ok",
            "reasoning": {"summary": "s"},
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "Bash",
                    "arguments": {"command": "ls"},
                    "extra": {"k": 1},
                },
                {"id": "c2", "name": "Deep", "arguments": {"a": inner}},
            ],
            "tool_call_id": None,
            "extra": {"m": True, "x": 1.5},
        },
        {
            "role": "system",
            "content": "be brief",
            "reasoning": None,
            "tool_calls": [],
            "tool_call_id": None,
        },
        {
            "role": "tool",
            "content": "",
            "reasoning": None,
            "tool_calls": [],
            "tool_call_id": "c1",
        },
    ]


def test_an_opening_discards_stale_drafts_which_never_count_as_open(
    enabled: Binary,
) -> None:
    """Item 9 "Opening": drafts untouched for longer than
    `NEOSIAN_DRAFT_SECONDS` go once the opening's checks have passed, their
    bytes in `freed_bytes`; a refused opening discards nothing."""
    drafts = enabled.home / "conversations" / SESSION / ".drafts"
    drafts.mkdir(parents=True)
    kept = record(0, 0, "user", "prompt", "hé")
    for index in range(16):
        state = drafts / f"old{index}.state"
        state.write_bytes(b"{")
        os.utime(state, (946684800, 946684800))
    (drafts / "old0.jsonl").write_text(record_line(kept), encoding="utf-8")
    stale = sum(bookkeeping(SESSION, f"old{index}") for index in range(16))
    stale += record_bytes(SESSION, kept)
    assert stale == 10 * 1_042 + 6 * 1_043 + (3 + 14 + 256)
    with serve(enabled) as server:
        bad = {"conversation_id": SESSION, "draft": "new", "records": [kept, kept]}
        refused(server, bad)
        assert len(list(drafts.glob("old*.state"))) == 16
        opened = write(server, SESSION, "new", prompts(1))
        assert opened == {
            "draft": "new",
            "state": "open",
            "next_seq": 1,
            "turn": None,
            "stored_bytes": record_bytes(SESSION, prompts(1)[0])
            + bookkeeping(SESSION, "new"),
            "freed_bytes": stale,
        }
        assert sorted(path.name for path in drafts.iterdir()) == [
            "new.jsonl",
            "new.state",
        ]
        for index in range(1, 16):
            again = write(server, SESSION, f"new{index}", prompts(1))
            assert (again["state"], again["freed_bytes"]) == ("open", 0)
        body = {"conversation_id": SESSION, "draft": "new16", "records": prompts(1)}
        assert conflict(server, SESSION, "drafts_open", body) == {"limit": 16}
    # Sixteen torn states with an old file time: the seventeenth then opens,
    # and each goes with its bookkeeping and the record its file holds.
    for path in drafts.glob("*.state"):
        path.write_bytes(b"{")
        os.utime(path, (946684800, 946684800))
    with serve(enabled, extra_env={"NEOSIAN_DRAFT_SECONDS": "2592000"}) as server:
        late = write(server, SESSION, "new16", prompts(1))
        assert late["state"] == "open"
        assert late["freed_bytes"] == 16 * record_bytes(SESSION, prompts(1)[0]) + sum(
            bookkeeping(SESSION, name)
            for name in ["new", *[f"new{index}" for index in range(1, 16)]]
        )
        assert probe(server, SESSION, "new") == nothing("new")
