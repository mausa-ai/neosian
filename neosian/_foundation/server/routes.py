"""The store-shaped routes — the wire mirrors the ABCs 1:1 (DESIGN §18).

Twelve POST endpoints under `/v1/`, one per storage-ABC method. Handlers
are thin: decode the parameters by name, await the store, encode the
return value under one key. Everything a store method raises —
`NeosianError` and the ABCs' bare `ValueError` — crosses as the §18
envelope at 400; anything else is a bare 500 the client propagates raw
(the ledger #39 posture). Validation stays server-side: the real stores
validate scope-then-path, one validator, one truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
)
from neosian._foundation.shared.exceptions import NeosianError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.memory.base import MemoryStore


def _bad_request(code: str, message: str) -> Response:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": {}}},
        status_code=400,
    )


def _endpoint(
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        try:
            payload: Any = await request.json()
        except ValueError:
            return _bad_request(VALUE_ERROR_CODE, "request body must be JSON")
        if not isinstance(payload, dict):
            return _bad_request(VALUE_ERROR_CODE, "request body must be an object")
        try:
            return JSONResponse(await handler(payload))
        except (NeosianError, ValueError) as exc:
            return JSONResponse({"error": encode_error(exc)}, status_code=400)
        except KeyError as exc:
            return _bad_request(VALUE_ERROR_CODE, f"missing parameter: {exc}")

    return endpoint


def store_routes(memory: MemoryStore, conversation: ConversationStore) -> list[Route]:
    """The twelve wire endpoints over one both-seams store."""

    async def read(payload: dict[str, Any]) -> dict[str, Any]:
        document = await memory.read(payload["scope"], payload["path"])
        return {"document": None if document is None else encode_document(document)}

    async def write(payload: dict[str, Any]) -> dict[str, Any]:
        document = await memory.write(
            payload["scope"],
            payload["path"],
            payload["content"],
            actor=payload.get("actor"),
            expected_version=payload.get("expected_version"),
        )
        return {"document": encode_document(document)}

    async def delete(payload: dict[str, Any]) -> dict[str, Any]:
        deleted = await memory.delete(
            payload["scope"], payload["path"], actor=payload.get("actor")
        )
        return {"deleted": deleted}

    async def rename(payload: dict[str, Any]) -> dict[str, Any]:
        document = await memory.rename(
            payload["scope"],
            payload["src"],
            payload["dst"],
            actor=payload.get("actor"),
        )
        return {"document": encode_document(document)}

    async def list_documents(payload: dict[str, Any]) -> dict[str, Any]:
        entries = await memory.list_documents(
            payload["scope"], prefix=payload.get("prefix", "")
        )
        return {"entries": [encode_entry(entry) for entry in entries]}

    async def versions(payload: dict[str, Any]) -> dict[str, Any]:
        rows = await memory.versions(
            payload["scope"], payload["path"], limit=payload.get("limit", 50)
        )
        return {"versions": [encode_version(row) for row in rows]}

    async def redact(payload: dict[str, Any]) -> dict[str, Any]:
        count = await memory.redact(
            payload["scope"], path=payload.get("path"), actor=payload.get("actor")
        )
        return {"count": count}

    async def append_turn(payload: dict[str, Any]) -> dict[str, Any]:
        turn = await conversation.append_turn(
            payload["conversation_id"],
            [message_from_json(encoded) for encoded in payload["messages"]],
        )
        return {"turn": encode_turn(turn)}

    async def read_turns(payload: dict[str, Any]) -> dict[str, Any]:
        turns = await conversation.read_turns(
            payload["conversation_id"],
            after=payload.get("after", 0),
            limit=payload.get("limit"),
        )
        return {"turns": [encode_turn(turn) for turn in turns]}

    async def last_turn_number(payload: dict[str, Any]) -> dict[str, Any]:
        return {"turn": await conversation.last_turn_number(payload["conversation_id"])}

    async def append_projections(payload: dict[str, Any]) -> dict[str, Any]:
        await conversation.append_projections(
            payload["conversation_id"],
            [decode_projection(encoded) for encoded in payload["entries"]],
        )
        return {}

    async def read_projections(payload: dict[str, Any]) -> dict[str, Any]:
        entries = await conversation.read_projections(
            payload["conversation_id"],
            after=payload.get("after", 0),
            limit=payload.get("limit"),
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
