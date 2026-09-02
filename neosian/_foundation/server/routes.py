"""The store-shaped routes — the wire mirrors the ABCs 1:1 (DESIGN §18).

Twelve POST endpoints under `/v1/`, one per storage-ABC method. Handlers
are thin: decode the parameters by name through the typed readers in
`wire.py`, await the store, encode the return value under one key.
Everything a store method raises — `NeosianError` and the ABCs' bare
`ValueError` — crosses as the §18 envelope at 400, and so does every
mis-typed or missing parameter; only infrastructure failures are a bare
500 the client propagates raw (the ledger #39 posture). Validation
stays server-side: the real stores validate scope-then-path, one
validator, one truth. A body over `MAX_REQUEST_BYTES` is 413 in the
same envelope — one authenticated client cannot hold the daemon's
memory hostage.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.llm.codec import message_from_json
from neosian._foundation.server.sdk import JSONResponse, Request, Response, Route
from neosian._foundation.server.wire import (
    VALUE_ERROR_CODE,
    decode_projection,
    encode_document,
    encode_entry,
    encode_error,
    encode_projection,
    encode_turn,
    encode_version,
    optional_int,
    optional_str,
    require_objects,
    require_str,
)
from neosian._foundation.shared.exceptions import NeosianError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.memory.base import MemoryStore

# Generous because a turn may carry base64 media blocks; bounded because
# the body is buffered whole before it is decoded.
MAX_REQUEST_BYTES: Final = 64 * 1024 * 1024


def _envelope(message: str, *, status: int = 400) -> Response:
    return JSONResponse(
        {"error": {"code": VALUE_ERROR_CODE, "message": message, "details": {}}},
        status_code=status,
    )


async def _read_body(request: Request) -> bytes | None:
    """The whole body, or None once it exceeds the ceiling."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_REQUEST_BYTES:
            return None
    return bytes(body)


def _endpoint(
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        body = await _read_body(request)
        if body is None:
            return _envelope(
                f"request body exceeds {MAX_REQUEST_BYTES} bytes", status=413
            )
        try:
            payload: Any = json.loads(body)
        except ValueError:
            return _envelope("request body must be JSON")
        if not isinstance(payload, dict):
            return _envelope("request body must be an object")
        try:
            return JSONResponse(await handler(payload))
        except (NeosianError, ValueError) as exc:
            return JSONResponse({"error": encode_error(exc)}, status_code=400)
        except (KeyError, TypeError) as exc:
            # The backstop beneath the typed readers: a malformed nested
            # object (a message without a role) is still the caller's.
            return _envelope(f"malformed request: {exc}")

    return endpoint


def store_routes(memory: MemoryStore, conversation: ConversationStore) -> list[Route]:
    """The twelve wire endpoints over one both-seams store."""

    async def read(payload: dict[str, Any]) -> dict[str, Any]:
        document = await memory.read(
            require_str(payload, "scope"), require_str(payload, "path")
        )
        return {"document": None if document is None else encode_document(document)}

    async def write(payload: dict[str, Any]) -> dict[str, Any]:
        document = await memory.write(
            require_str(payload, "scope"),
            require_str(payload, "path"),
            require_str(payload, "content"),
            actor=optional_str(payload, "actor"),
            expected_version=optional_int(payload, "expected_version"),
        )
        return {"document": encode_document(document)}

    async def delete(payload: dict[str, Any]) -> dict[str, Any]:
        deleted = await memory.delete(
            require_str(payload, "scope"),
            require_str(payload, "path"),
            actor=optional_str(payload, "actor"),
        )
        return {"deleted": deleted}

    async def rename(payload: dict[str, Any]) -> dict[str, Any]:
        document = await memory.rename(
            require_str(payload, "scope"),
            require_str(payload, "src"),
            require_str(payload, "dst"),
            actor=optional_str(payload, "actor"),
        )
        return {"document": encode_document(document)}

    async def list_documents(payload: dict[str, Any]) -> dict[str, Any]:
        entries = await memory.list_documents(
            require_str(payload, "scope"), prefix=optional_str(payload, "prefix") or ""
        )
        return {"entries": [encode_entry(entry) for entry in entries]}

    async def versions(payload: dict[str, Any]) -> dict[str, Any]:
        limit = optional_int(payload, "limit")
        rows = await memory.versions(
            require_str(payload, "scope"),
            require_str(payload, "path"),
            limit=50 if limit is None else limit,
        )
        return {"versions": [encode_version(row) for row in rows]}

    async def redact(payload: dict[str, Any]) -> dict[str, Any]:
        count = await memory.redact(
            require_str(payload, "scope"),
            path=optional_str(payload, "path"),
            actor=optional_str(payload, "actor"),
        )
        return {"count": count}

    async def append_turn(payload: dict[str, Any]) -> dict[str, Any]:
        turn = await conversation.append_turn(
            require_str(payload, "conversation_id"),
            [message_from_json(e) for e in require_objects(payload, "messages")],
        )
        return {"turn": encode_turn(turn)}

    async def read_turns(payload: dict[str, Any]) -> dict[str, Any]:
        turns = await conversation.read_turns(
            require_str(payload, "conversation_id"),
            after=optional_int(payload, "after") or 0,
            limit=optional_int(payload, "limit"),
        )
        return {"turns": [encode_turn(turn) for turn in turns]}

    async def last_turn_number(payload: dict[str, Any]) -> dict[str, Any]:
        number = await conversation.last_turn_number(
            require_str(payload, "conversation_id")
        )
        return {"turn": number}

    async def append_projections(payload: dict[str, Any]) -> dict[str, Any]:
        await conversation.append_projections(
            require_str(payload, "conversation_id"),
            [decode_projection(e) for e in require_objects(payload, "entries")],
        )
        return {}

    async def read_projections(payload: dict[str, Any]) -> dict[str, Any]:
        entries = await conversation.read_projections(
            require_str(payload, "conversation_id"),
            after=optional_int(payload, "after") or 0,
            limit=optional_int(payload, "limit"),
        )
        return {"entries": [encode_projection(entry) for entry in entries]}

    handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
        "memory/read": read,
        "memory/write": write,
        "memory/delete": delete,
        "memory/rename": rename,
        "memory/list_documents": list_documents,
        "memory/versions": versions,
        "memory/redact": redact,
        "conversation/append_turn": append_turn,
        "conversation/read_turns": read_turns,
        "conversation/last_turn_number": last_turn_number,
        "conversation/append_projections": append_projections,
        "conversation/read_projections": read_projections,
    }
    return [
        Route(f"/v1/{name}", _endpoint(handler), methods=["POST"])
        for name, handler in handlers.items()
    ]
