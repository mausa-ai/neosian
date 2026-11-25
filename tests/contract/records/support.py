"""Turn records over raw HTTP, written from DESIGN §40.54 with no product code.

A record is built key by key in the design's order, the size rule of item 14
is computed here from the request alone, and a refusal is read as the raw
envelope. Nothing is taken from what the binary prints.
"""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from tests.contract.support import Binary, Reply, Server

ROUTE = "conversation/write_records"
MARKER = '{"neosian_records":1}\n'
ANSWER_KEYS = ["draft", "state", "next_seq", "turn", "stored_bytes", "freed_bytes"]
HEADER_KEYS = ["turn", "created_at", "actor", "redacted", "records", "bytes"]
BODY_CEILING = 4_194_304
PART_PREFIX = "request body exceeds "
BOUND_PREFIX = "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: "


def enable(binary: Binary) -> None:
    """The marker `neosian records enable` writes (item 4.1)."""
    (binary.home / ".records").mkdir(parents=True)
    (binary.home / ".records" / "format").write_text(MARKER, encoding="utf-8")


def record(
    seq: int,
    message: int,
    role: str,
    kind: str,
    text: str,
    *,
    content: str | None = "text",
    call: int | None = None,
    call_id: str | None = None,
    name: str | None = None,
    chunk: int = 0,
    size: int | None = None,
) -> dict[str, Any]:
    """One record in write shape, every key in item 3.1's order."""
    return {
        "seq": seq,
        "message": message,
        "role": role,
        "kind": kind,
        "content": content,
        "block": None,
        "call": call,
        "call_id": call_id,
        "name": name,
        "chunk": chunk,
        "size": len(text.encode()) if size is None else size,
        "text": text,
    }


def prompts(count: int, start: int = 0) -> list[dict[str, Any]]:
    """`count` one-letter user messages, one record each, from `start`."""
    return [
        record(index, index, "user", "prompt", "a")
        for index in range(start, start + count)
    ]


def u(value: str | None) -> int:
    return 0 if value is None else len(value.encode())


def record_bytes(conversation: str, item: dict[str, Any]) -> int:
    """Item 14: `u(text) + u(call_id) + u(name) + 2c + 256`."""
    named = u(item.get("call_id")) + u(item.get("name"))
    return u(item["text"]) + named + 2 * u(conversation) + 256


def records_bytes(conversation: str, items: list[dict[str, Any]]) -> int:
    return sum(record_bytes(conversation, item) for item in items)


def bookkeeping(conversation: str, draft: str) -> int:
    """Item 14: `o + 2c + 1024`, the first request of a draft."""
    return u(draft) + 2 * u(conversation) + 1024


def header_bytes(conversation: str, draft: str, actor: str | None) -> int:
    """Item 14: `u(actor) + o + 4c + 512`, at the seal."""
    return u(actor) + u(draft) + 4 * u(conversation) + 512


def conversation_row(conversation: str) -> int:
    """Item 14: `c + 256`, a seal that creates the conversation."""
    return u(conversation) + 256


def write(
    server: Server, conversation: str, draft: str, items: list[Any], **more: Any
) -> dict[str, Any]:
    """One accepted request; request and answer checked against the OpenAPI."""
    body: dict[str, Any] = server.post(
        ROUTE, conversation_id=conversation, draft=draft, records=items, **more
    )
    assert list(body) == ANSWER_KEYS
    if body["turn"] is not None:
        assert list(body["turn"]) == HEADER_KEYS
    return body


def send(server: Server, body: Any, *, token: str | None = None) -> Reply:
    return server.request("/v1/" + ROUTE, body, token=token)


def refused(
    server: Server, body: Any, status: int = 400, code: str = "value_error"
) -> dict[str, Any]:
    """A refusal's envelope, its three keys exactly."""
    reply = send(server, body)
    assert reply.status == status, reply
    assert list(reply.body) == ["error"], reply
    error: dict[str, Any] = reply.body["error"]
    assert list(error) == ["code", "message", "details"], reply
    assert error["code"] == code, reply
    return error


def conflict(
    server: Server, conversation: str, reason: str, body: dict[str, Any]
) -> dict[str, Any]:
    """A conflict's details beyond `conversation_id` and `reason` (item 8.2)."""
    error = refused(server, body, 400, "agent_conversation_conflict")
    assert error["message"] == f"Conversation conflict on '{conversation}': {reason}"
    details: dict[str, Any] = dict(error["details"])
    assert details.pop("conversation_id") == conversation
    assert details.pop("reason") == reason
    return details


def probe(server: Server, conversation: str, draft: str) -> dict[str, Any]:
    return write(server, conversation, draft, [])


def nothing(draft: str) -> dict[str, Any]:
    """The probe of a draft that does not exist."""
    return {
        "draft": draft,
        "state": "none",
        "next_seq": 0,
        "turn": None,
        "stored_bytes": 0,
        "freed_bytes": 0,
    }


def declared(
    server: Server, path: str, length: int, *, token: str | None = None
) -> tuple[str, bytes]:
    """The status line and body a POST is answered when it declares a body of
    `length` bytes and sends none of it; no bearer unless `token` is given."""
    address = urlsplit(server.url)
    assert address.hostname is not None and address.port is not None
    head = f"POST {path} HTTP/1.1\r\nHost: {address.netloc}\r\n"
    if token is not None:
        head += f"Authorization: Bearer {token}\r\n"
    head += f"Content-Length: {length}\r\nConnection: close\r\n\r\n"
    with socket.create_connection((address.hostname, address.port), 10) as link:
        link.sendall(head.encode())
        answer = b""
        while chunk := link.recv(65_536):
            answer += chunk
    headers, _, body = answer.partition(b"\r\n\r\n")
    return headers.split(b"\r\n")[0].decode(), body


def raw(server: Server, body: Any) -> tuple[int, bytes]:
    """The status and the answer's bytes as sent, for key order and length."""
    request = Request(
        f"{server.url}/v1/{ROUTE}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {server.token}"},
        method="POST",
    )
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def compact(value: Any) -> bytes:
    """Compact JSON, non-ASCII raw, keys in the order given."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
