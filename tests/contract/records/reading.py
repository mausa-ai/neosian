"""`conversation/read_records` over raw HTTP, written from DESIGN §40.54
items 3 and 10 with no product code.

An answered record is the written record with its turn in front and `tail`
and `truncated` behind (item 3.1). A page's fill is computed here from the
answer alone: an item costs its compact encoding and one separator, and a
turn's header goes with that turn's first record on the page as one group.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from tests.contract.records.support import HEADER_KEYS, compact, record
from tests.contract.support import Reply, Server

READ = "conversation/read_records"
PAGE_KEYS = ["turns", "records", "continuation"]
RECORD_KEYS = [
    "turn",
    "seq",
    "message",
    "role",
    "kind",
    "content",
    "block",
    "call",
    "call_id",
    "name",
    "chunk",
    "size",
    "text",
    "tail",
    "truncated",
]
KINDS = [
    "prompt",
    "reply",
    "system",
    "tool_result",
    "tool_call",
    "reasoning",
    "attachment",
    "extra",
]
DEFAULT_PAGE = 983_040
RECORD_BYTES = 81_920
FRAMING = 4_160
IMAGE = '{"type":"image","media_type":"image/png","data":"QUJD","url":null}'

# One turn holding all eight kinds (items 3.2 and 3.3), as messages and as
# the records those messages make, in the order a message gives them.
EVERY_MESSAGES: list[dict[str, Any]] = [
    {
        "role": "system",
        "content": "be brief",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    },
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "look"},
            {"type": "image", "media_type": "image/png", "data": "QUJD", "url": None},
        ],
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    },
    {
        "role": "assistant",
        "content": "on it",
        "reasoning": {"summary": "s"},
        "tool_calls": [
            {
                "id": "c1",
                "name": "Bash",
                "arguments": {"command": "ls"},
                "extra": {"k": 1},
            }
        ],
        "tool_call_id": None,
        "extra": {"m": True},
    },
    {
        "role": "tool",
        "content": "a.txt",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": "c1",
    },
]
EVERY: list[dict[str, Any]] = [
    record(0, 0, "system", "system", "be brief"),
    {**record(1, 1, "user", "prompt", "look", content="blocks"), "block": 0},
    {**record(2, 1, "user", "attachment", IMAGE, content=None), "block": 1},
    record(3, 2, "assistant", "reply", "on it"),
    record(
        4,
        2,
        "assistant",
        "tool_call",
        '{"command":"ls"}',
        content=None,
        call=0,
        call_id="c1",
        name="Bash",
    ),
    record(5, 2, "assistant", "extra", '{"k":1}', content=None, call=0),
    record(6, 2, "assistant", "reasoning", '{"summary":"s"}', content=None),
    record(7, 2, "assistant", "extra", '{"m":true}', content=None),
    record(8, 3, "tool", "tool_result", "a.txt", call_id="c1"),
]
EVERY_BYTES = 8 + 4 + 66 + 5 + 16 + 7 + 15 + 10 + 5


def answered(
    turn: int,
    item: dict[str, Any],
    *,
    tail: str | None = None,
    truncated: bool = False,
    **changed: Any,
) -> dict[str, Any]:
    """A written record as a read answers it, all fifteen keys in order."""
    return {"turn": turn, **item, **changed, "tail": tail, "truncated": truncated}


def all_answered(turn: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [answered(turn, item) for item in items]


def header(
    turn: dict[str, Any], records: int, size: int, *, redacted: bool = False
) -> dict[str, Any]:
    """The header of a turn `read_turns` answered: its number, time and
    actor, with the records and bytes computed by the caller."""
    return {
        "turn": turn["turn"],
        "created_at": turn["created_at"],
        "actor": turn["actor"],
        "redacted": redacted,
        "records": records,
        "bytes": size,
    }


def post(server: Server, body: Any, *, token: str | None = None) -> Reply:
    return server.request("/v1/" + READ, body, token=token)


def read(server: Server, conversation: str, **more: Any) -> dict[str, Any]:
    """One accepted page; request and answer checked against the OpenAPI
    and the order of every key checked as parsed."""
    body: dict[str, Any] = server.post(READ, conversation_id=conversation, **more)
    assert list(body) == PAGE_KEYS
    for turn in body["turns"]:
        assert list(turn) == HEADER_KEYS
    for item in body["records"]:
        assert list(item) == RECORD_KEYS
    return body


def refusal(
    server: Server, body: Any, status: int = 400, code: str = "value_error"
) -> dict[str, Any]:
    """A refusal's envelope, its three keys exactly."""
    reply = post(server, body)
    assert reply.status == status, reply
    assert list(reply.body) == ["error"], reply
    error: dict[str, Any] = reply.body["error"]
    assert list(error) == ["code", "message", "details"], reply
    assert error["code"] == code, reply
    return error


def message(server: Server, body: Any) -> str:
    """The message of a `400 value_error` with empty details."""
    error = refusal(server, body)
    assert error["details"] == {}
    return str(error["message"])


def raw(server: Server, body: Any) -> tuple[int, bytes]:
    """The status and the answer's bytes as sent."""
    request = Request(
        f"{server.url}/v1/{READ}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {server.token}"},
        method="POST",
    )
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        data: bytes = response.read()
        assert response.headers["Content-Length"] == str(len(data))
        return response.status, data


def page_bytes(turns: list[Any], records: list[Any], continuation: Any) -> bytes:
    """A page as item 10 orders it: `turns`, `records`, `continuation`."""
    return compact({"turns": turns, "records": records, "continuation": continuation})


def cost(item: dict[str, Any]) -> int:
    """What an item costs a page: its compact encoding and one separator."""
    return len(compact(item)) + 1


def groups(page: dict[str, Any]) -> list[int]:
    """The costs of a page's groups, in order: a turn's header with its
    first record on the page, then each further record alone; a header
    alone for a turn with no record on the page."""
    sizes: list[int] = []
    left = list(page["records"])
    for turn in page["turns"]:
        first = cost(turn)
        own = 0
        while left and left[0]["turn"] == turn["turn"]:
            sizes.append(cost(left.pop(0)) + (first if own == 0 else 0))
            own += 1
        if own == 0:
            sizes.append(first)
    assert left == [], "every record's turn has its header on the page, in order"
    return sizes


def pages(
    server: Server,
    conversation: str,
    asked: int | None,
    *,
    limit: int | None = None,
    start: str | None = None,
    **selection: Any,
) -> list[dict[str, Any]]:
    """Every page of one read, following its continuations, from `start`
    when a continuation is given. Each page holds its groups while their
    costs stay within the limit, always one group, and as many as fit: the
    next page's first group would not have."""
    bound = (DEFAULT_PAGE if asked is None else asked) if limit is None else limit
    sized = {} if asked is None else {"page_bytes": asked}
    answers: list[dict[str, Any]] = []
    continuation = start
    while True:
        more = {} if continuation is None else {"continuation": continuation}
        page = read(server, conversation, **selection, **sized, **more)
        sizes = groups(page)
        assert sizes or not answers, "a later page is never empty"
        assert len(sizes) <= 1 or sum(sizes) <= bound
        if answers:
            assert sum(groups(answers[-1])) + sizes[0] > bound, "a page is full"
        answers.append(page)
        continuation = page["continuation"]
        if continuation is None:
            return answers
        assert 1 <= len(continuation.encode()) <= 4_096
        assert len(answers) < 5_000


def joined(answers: list[dict[str, Any]]) -> tuple[list[Any], list[Any]]:
    """The headers and records of every page: a header repeated at the
    start of the next page, because that page goes on inside its turn, is
    counted once."""
    turns: list[Any] = []
    records: list[Any] = []
    for page in answers:
        for index, turn in enumerate(page["turns"]):
            if turns and turns[-1]["turn"] == turn["turn"]:
                assert index == 0 and turns[-1] == turn
                continue
            turns.append(turn)
        records.extend(page["records"])
    return turns, records


def forged(token: str, turn: int, seq: int) -> str:
    """A continuation made up from a real one: the same method and the
    same fingerprint of the selection, another place, no file hints. The
    token is base64 of compact JSON, as its first characters show."""
    method, fingerprint, *_ = json.loads(base64.urlsafe_b64decode(token))
    assert method == "records-v1/read" and len(fingerprint) == 64
    place = json.dumps([method, fingerprint, turn, seq, 0, 0, 0], separators=(",", ":"))
    return base64.urlsafe_b64encode(place.encode()).decode()


def encoding(text: str) -> int:
    """A text's compact JSON string encoding, quotes included, in bytes."""
    return len(json.dumps(text, ensure_ascii=False).encode())


def head_of(text: str, size: int) -> str:
    """The longest prefix whose encoding is at most `size` bytes."""
    fits = [n for n in range(len(text) + 1) if encoding(text[:n]) <= size]
    return text[: max(fits)] if fits else ""


def tail_of(text: str, size: int) -> str:
    """The longest suffix whose encoding is at most `size` bytes."""
    fits = [n for n in range(len(text) + 1) if encoding(text[n:]) <= size]
    return text[min(fits) :] if fits else ""
