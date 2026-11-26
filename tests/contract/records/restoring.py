"""`store/restore_records` over raw HTTP, written from DESIGN §40.54 items
8.2, 12 and 14 with no product code.

A restore's content is one stream: each turn's header then its records,
then the projections, then the trail acts. Every item is built here key by
key in the design's order, its bytes by the size rule and the unit digest
by SHA-256 over the SHA-256 of each item's compact JSON, computed from the
request alone and never taken from what the binary prints.
"""

from __future__ import annotations

import hashlib
from typing import Any

from tests.contract.records.searching import post, refusal
from tests.contract.records.support import compact, record, record_bytes, u
from tests.contract.support import Reply, Server

RESTORE = "store/restore_records"
ANSWER_KEYS = ["restore", "state", "position", "stored_bytes", "freed_bytes", "digest"]
POSITION_KEYS = ["turn", "seq", "complete", "projections", "redactions"]
ANSWER_BOUND = 2_048
ITEMS = 2_048

# Item 12's example: `cc-7f3a` under the restore id item 22 derives for it.
SESSION = "cc-7f3a"
ID = "iecf9f710d465bc0ea0fe2fbba8915596"
ACTOR = "claude-code:7f3a"
DIGEST = "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32"


def header(
    turn: int,
    created_at: str,
    actor: str | None,
    records: int,
    size: int,
    *,
    redacted: bool = False,
) -> dict[str, Any]:
    """A turn header, every key in item 3.6's order."""
    return {
        "turn": turn,
        "created_at": created_at,
        "actor": actor,
        "redacted": redacted,
        "records": records,
        "bytes": size,
    }


def placed(turn: int, item: dict[str, Any]) -> dict[str, Any]:
    """A record in restore shape: its turn in front of the write shape."""
    return {"turn": turn, **item}


def projection(turn: int, kind: str, text: str, span: int = 1) -> dict[str, Any]:
    return {"turn": turn, "kind": kind, "text": text, "span": span}


def act(
    conversation: str, turns: list[int], actor: str | None, created_at: str
) -> dict[str, Any]:
    return {
        "conversation_id": conversation,
        "turns": turns,
        "actor": actor,
        "created_at": created_at,
    }


def unit_digest(stream: list[dict[str, Any]]) -> str:
    """Item 12: SHA-256 over the 32 raw bytes of each item's own SHA-256,
    in stream order."""
    whole = hashlib.sha256()
    for item in stream:
        whole.update(hashlib.sha256(compact(item)).digest())
    return whole.hexdigest()


def turn_header_bytes(conversation: str, restore: str, item: dict[str, Any]) -> int:
    """Item 14: `u(actor) + o + 4c + 512`, or `u(actor) + 2c + 256` redacted."""
    c = u(conversation)
    if item["redacted"]:
        return u(item["actor"]) + 2 * c + 256
    return u(item["actor"]) + u(restore) + 4 * c + 512


def projection_bytes(conversation: str, item: dict[str, Any]) -> int:
    """Item 14: `u(text) + 2c + 128`."""
    return u(item["text"]) + 2 * u(conversation) + 128


def act_bytes(conversation: str, item: dict[str, Any]) -> int:
    """Item 14: `u(actor) + 4 * len(turns) + 2c + 128`."""
    return u(item["actor"]) + 4 * len(item["turns"]) + 2 * u(conversation) + 128


def restore_bookkeeping(conversation: str, restore: str) -> int:
    """Item 14: `o + 2c + 1024`, the first part; freed at commit or discard."""
    return u(restore) + 2 * u(conversation) + 1024


def marker(conversation: str, restore: str) -> int:
    """Item 14: `o + 2c + 256`, the committed marker."""
    return u(restore) + 2 * u(conversation) + 256


def parent(conversation: str) -> int:
    """Item 14: `c + 256`, a commit that creates the conversation."""
    return u(conversation) + 256


def content_bytes(conversation: str, restore: str, body: dict[str, Any]) -> int:
    """The size-rule bytes of one part's items, all of them new."""
    return (
        sum(turn_header_bytes(conversation, restore, h) for h in body.get("turns", []))
        + sum(record_bytes(conversation, r) for r in body.get("records", []))
        + sum(projection_bytes(conversation, p) for p in body.get("projections", []))
        + sum(act_bytes(conversation, a) for a in body.get("redactions", []))
    )


def position(
    turn: int, seq: int, complete: bool, projections: int = 0, redactions: int = 0
) -> dict[str, Any]:
    return {
        "turn": turn,
        "seq": seq,
        "complete": complete,
        "projections": projections,
        "redactions": redactions,
    }


def answer(
    restore: str,
    state: str,
    at: dict[str, Any] | None,
    stored: int = 0,
    freed: int = 0,
    digest: str | None = None,
) -> dict[str, Any]:
    return {
        "restore": restore,
        "state": state,
        "position": at,
        "stored_bytes": stored,
        "freed_bytes": freed,
        "digest": digest,
    }


def send(server: Server, conversation: str, restore: str, **more: Any) -> Reply:
    return post(
        server, RESTORE, {"conversation_id": conversation, "restore": restore, **more}
    )


def part(
    server: Server, conversation: str, restore: str, **more: Any
) -> dict[str, Any]:
    """One accepted request; request and answer checked against the OpenAPI
    and the keys of the answer in item 12's order."""
    body: dict[str, Any] = server.post(
        RESTORE, conversation_id=conversation, restore=restore, **more
    )
    assert list(body) == ANSWER_KEYS
    if body["position"] is not None:
        assert list(body["position"]) == POSITION_KEYS
    assert body["restore"] == restore
    return body


def status(server: Server, conversation: str, restore: str) -> dict[str, Any]:
    """The status probe: only `conversation_id` and `restore`."""
    return part(server, conversation, restore)


def refused(
    server: Server, body: dict[str, Any], status: int = 400, code: str = "value_error"
) -> dict[str, Any]:
    return refusal(server, RESTORE, body, status, code)


def why(server: Server, body: dict[str, Any]) -> str:
    """The message of a `400 value_error` with empty details."""
    error = refused(server, body)
    assert error["details"] == {}
    return str(error["message"])


def conflict(
    server: Server, conversation: str, reason: str, **more: Any
) -> dict[str, Any]:
    """A conflict's envelope: its details in item 8.2's key order,
    `conversation_id` and `reason` first; what follows them is returned."""
    body = {"conversation_id": conversation, **more}
    error = refused(server, body, 400, "agent_conversation_conflict")
    assert error["message"] == f"Conversation conflict on '{conversation}': {reason}"
    details: dict[str, Any] = error["details"]
    assert list(details)[:2] == ["conversation_id", "reason"]
    assert (details["conversation_id"], details["reason"]) == (conversation, reason)
    rest = {
        key: value for key, value in details.items() if key not in list(details)[:2]
    }
    if "position" in rest:
        assert list(rest["position"]) == POSITION_KEYS
    return rest


# The example's source: turn 1 a system, a user and an assistant message of
# 32, 500 and 1,000 bytes, turn 2 redacted, turn 3 a prompt of 44 bytes; one
# epoch projection and one trail act.
ONE = [
    placed(1, record(0, 0, "system", "system", "x" * 32)),
    placed(1, record(1, 1, "user", "prompt", "y" * 500)),
    placed(1, record(2, 2, "assistant", "reply", "z" * 1000)),
]
THREE = [placed(3, record(0, 0, "user", "prompt", "w" * 44))]
HEADERS = [
    header(1, "2026-10-02T09:15:00.000001Z", ACTOR, 3, 1532),
    header(2, "2026-10-02T09:20:11Z", ACTOR, 0, 0, redacted=True),
    header(3, "2026-10-02T09:31:40Z", ACTOR, 1, 44),
]
EPOCH = projection(3, "epoch", "set up the cache", 3)
ERASED = act(SESSION, [2], "client:default/ops", "2026-10-05T08:00:00Z")
FIRST: dict[str, Any] = {"turns": HEADERS[:1], "records": ONE}
SECOND: dict[str, Any] = {
    "turns": HEADERS[1:],
    "records": THREE,
    "projections_from": 0,
    "projections": [EPOCH],
    "redactions_from": 0,
    "redactions": [ERASED],
}
STREAM = [HEADERS[0], *ONE, HEADERS[1], HEADERS[2], *THREE, EPOCH, ERASED]
END = position(3, 1, True, 1, 1)


def message(role: str, text: str) -> dict[str, Any]:
    return {
        "role": role,
        "content": text,
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    }


def whole_turn(conversation: str, number: int, messages: list[Any]) -> dict[str, Any]:
    """A turn of the example as `read_turns` answers it."""
    head = HEADERS[number - 1]
    return {
        "conversation_id": conversation,
        "turn": number,
        "messages": messages,
        "created_at": head["created_at"],
        "actor": head["actor"],
        "redacted": head["redacted"],
    }


TURNS = [
    whole_turn(
        SESSION,
        1,
        [
            message("system", "x" * 32),
            message("user", "y" * 500),
            message("assistant", "z" * 1000),
        ],
    ),
    whole_turn(SESSION, 2, []),
    whole_turn(SESSION, 3, [message("user", "w" * 44)]),
]
