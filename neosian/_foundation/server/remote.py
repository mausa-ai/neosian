"""RemoteStore — both storage ABCs over the state process's wire (§18).

The client half of the daemon: every method is one POST against the
store-shaped API, decoded by the shared `wire` codec, so a `RemoteStore`
caller catches exactly what a `FileStore` caller catches. httpx is a core
dependency — this module stays extra-free and never imports the serving
side (`sdk.py`, starlette, uvicorn).

Capability is transmitted, never claimed (the NM ruling): construct via
`await RemoteStore.connect(url, token=...)`, which performs one
capabilities handshake, refuses wire-version skew loudly, and returns an
instance whose class-level `supports_optimistic_concurrency` mirrors the
backend (True over Postgres, False over FileStore) — honest from birth,
because the conformance kits read the flag off `type(store)`. The plain
constructor stays pure (the PostgresStore shape) and keeps the class
default `False`.

Failure posture: neosian errors round-trip through the envelope;
non-envelope transport failures (connection refused, timeouts, a 401
after connect, a bare 500) propagate the raw httpx error — the ledger
#39 shape: infrastructure failures are the driver's, never re-dressed.
No retries in v1; the timeout is explicit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.llm.codec import message_to_json
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.journal import since_window
from neosian._foundation.server.wire import (
    WIRE_VERSION,
    decode_document,
    decode_entry,
    decode_error,
    decode_projection,
    decode_redaction,
    decode_turn,
    decode_version,
    encode_projection,
    encode_timestamp,
)
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from types import TracebackType

    from neosian._foundation.conversation.types import (
        ConversationProjection,
        ConversationTurn,
    )
    from neosian._foundation.llm.base import Message
    from neosian._foundation.memory.types import (
        MemoryDocument,
        MemoryEntry,
        MemoryRedaction,
        MemoryVersion,
    )


class RemoteStore(MemoryStore, ConversationStore):
    """Both storage ABCs over HTTP, against a running `neosian serve`.

    Construct with `await RemoteStore.connect(url, token=...)` — the
    handshake mirrors the backend's capability. The plain constructor is
    pure validation (tests, advanced embedding) and keeps the class
    default `supports_optimistic_concurrency = False`.

    `transport=` injects an `httpx.AsyncBaseTransport` — the ASGI test
    seam (`httpx.ASGITransport(app=...)` drives the app in-process).
    """

    supports_optimistic_concurrency: ClassVar[bool] = False

    def __init__(
        self,
        url: str,
        *,
        token: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Who the server makes this connection (§20), learned at `connect`;
        # every write through it records `<client>[/<actor>]`.
        self.client: str | None = None
        if not url.startswith(("http://", "https://")):
            raise ConfigurationError(f"RemoteStore url must be http(s), got {url!r}")
        if not token:
            raise ConfigurationError(
                "RemoteStore requires a bearer token (the server refuses "
                "unauthenticated requests; see NEOSIAN_SERVE_TOKEN)"
            )
        if timeout <= 0:
            raise ConfigurationError(f"timeout must be > 0, got {timeout}")
        self._base_url = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # Lifetime --------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client; idempotent."""
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    async def __aenter__(self) -> RemoteStore:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # The handshake ---------------------------------------------------------

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        token: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> RemoteStore:
        """One capabilities handshake; the returned instance's class
        mirrors the backend's `supports_optimistic_concurrency`."""
        probe = cls(url, token=token, timeout=timeout, transport=transport)
        try:
            capabilities = await probe.capabilities()
        except httpx.HTTPStatusError as exc:
            await probe.aclose()
            if exc.response.status_code == 401:
                raise ConfigurationError(
                    "the server rejected the bearer token — it must equal "
                    "the server's NEOSIAN_SERVE_TOKEN"
                ) from exc
            raise
        except BaseException:
            await probe.aclose()
            raise
        wire = capabilities.get("wire_version")
        if wire != WIRE_VERSION:
            await probe.aclose()
            raise ConfigurationError(
                f"wire version skew: the server speaks {wire!r}, this client "
                f"speaks {WIRE_VERSION} — align the neosian versions on both "
                "ends (the wire is versioned with the library, DESIGN §18)"
            )
        target: type[RemoteStore] = (
            _OptimisticRemoteStore
            if capabilities.get("supports_optimistic_concurrency")
            else RemoteStore
        )
        client = capabilities.get("client")
        if type(probe) is not target:
            await probe.aclose()
            probe = target(url, token=token, timeout=timeout, transport=transport)
        probe.client = client if isinstance(client, str) else None
        return probe

    async def capabilities(self) -> dict[str, Any]:
        """GET /v1/capabilities verbatim (wire version, backend, capability)."""
        response = await self._get_client().get("/v1/capabilities")
        response.raise_for_status()
        data: Any = response.json()
        return data if isinstance(data, dict) else {}

    # One POST per ABC method -----------------------------------------------

    async def _call(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._get_client().post(f"/v1/{endpoint}", json=payload)
        if response.is_success:
            data: Any = response.json()
            if not isinstance(data, dict):
                raise ValueError(f"malformed wire response for {endpoint!r}")
            return data
        envelope: Any = None
        try:
            body: Any = response.json()
            if isinstance(body, dict):
                envelope = body.get("error")
        except ValueError:
            envelope = None
        if isinstance(envelope, dict):
            raise decode_error(envelope)
        response.raise_for_status()
        raise AssertionError  # pragma: no cover - raise_for_status raised

    # MemoryStore -----------------------------------------------------------

    async def read(self, scope: str, path: str) -> MemoryDocument | None:
        data = await self._call("memory/read", {"scope": scope, "path": path})
        document = data["document"]
        return None if document is None else decode_document(document)

    async def write(
        self,
        scope: str,
        path: str,
        content: str,
        *,
        actor: str | None = None,
        expected_version: int | None = None,
    ) -> MemoryDocument:
        data = await self._call(
            "memory/write",
            {
                "scope": scope,
                "path": path,
                "content": content,
                "actor": actor,
                "expected_version": expected_version,
            },
        )
        return decode_document(data["document"])

    async def delete(self, scope: str, path: str, *, actor: str | None = None) -> bool:
        data = await self._call(
            "memory/delete", {"scope": scope, "path": path, "actor": actor}
        )
        return bool(data["deleted"])

    async def rename(
        self, scope: str, src: str, dst: str, *, actor: str | None = None
    ) -> MemoryDocument:
        data = await self._call(
            "memory/rename", {"scope": scope, "src": src, "dst": dst, "actor": actor}
        )
        return decode_document(data["document"])

    async def list_documents(
        self, scope: str, *, prefix: str = ""
    ) -> tuple[MemoryEntry, ...]:
        data = await self._call(
            "memory/list_documents", {"scope": scope, "prefix": prefix}
        )
        return tuple(decode_entry(entry) for entry in data["entries"])

    async def versions(
        self, scope: str, path: str, *, limit: int = 50
    ) -> tuple[MemoryVersion, ...]:
        data = await self._call(
            "memory/versions", {"scope": scope, "path": path, "limit": limit}
        )
        return tuple(decode_version(row) for row in data["versions"])

    async def redact(
        self, scope: str, *, path: str | None = None, actor: str | None = None
    ) -> int:
        data = await self._call(
            "memory/redact", {"scope": scope, "path": path, "actor": actor}
        )
        return int(data["count"])

    async def history(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryVersion, ...]:
        since_window(since, limit)  # naive `since` never crosses (C4)
        data = await self._call(
            "memory/history",
            {
                "scope": scope,
                "since": None if since is None else encode_timestamp(since),
                "limit": limit,
            },
        )
        return tuple(decode_version(row) for row in data["versions"])

    async def redactions(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryRedaction, ...]:
        since_window(since, limit)
        data = await self._call(
            "memory/redactions",
            {
                "scope": scope,
                "since": None if since is None else encode_timestamp(since),
                "limit": limit,
            },
        )
        return tuple(decode_redaction(act) for act in data["redactions"])

    # ConversationStore -----------------------------------------------------

    async def append_turn(
        self,
        conversation_id: str,
        messages: Sequence[Message],
        *,
        actor: str | None = None,
    ) -> ConversationTurn:
        data = await self._call(
            "conversation/append_turn",
            {
                "conversation_id": conversation_id,
                "messages": [message_to_json(message) for message in messages],
                "actor": actor,
            },
        )
        return decode_turn(data["turn"])

    async def read_turns(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationTurn, ...]:
        data = await self._call(
            "conversation/read_turns",
            {"conversation_id": conversation_id, "after": after, "limit": limit},
        )
        return tuple(decode_turn(turn) for turn in data["turns"])

    async def last_turn_number(self, conversation_id: str) -> int:
        data = await self._call(
            "conversation/last_turn_number", {"conversation_id": conversation_id}
        )
        return int(data["turn"])

    async def append_projections(
        self, conversation_id: str, entries: Sequence[ConversationProjection]
    ) -> None:
        await self._call(
            "conversation/append_projections",
            {
                "conversation_id": conversation_id,
                "entries": [encode_projection(entry) for entry in entries],
            },
        )

    async def read_projections(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationProjection, ...]:
        data = await self._call(
            "conversation/read_projections",
            {"conversation_id": conversation_id, "after": after, "limit": limit},
        )
        return tuple(decode_projection(entry) for entry in data["entries"])


class _OptimisticRemoteStore(RemoteStore):
    """The capability-mirroring twin `connect` returns over a backend that
    arbitrates `expected_version` races (Postgres). Never constructed
    directly — the handshake decides."""

    supports_optimistic_concurrency: ClassVar[bool] = True
