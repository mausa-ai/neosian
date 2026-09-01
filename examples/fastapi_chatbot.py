"""Example: a multi-tenant FastAPI chatbot on PostgresStore (N3).

Each worker process owns one `PostgresStore` (one connection pool); every
request builds a `Conversation` over it, so any worker can serve any turn
of any thread — the store's optimistic concurrency keeps concurrent
workers on one conversation gapless. Memory is mounted per tenant: a
read-write per-user mount plus a read-only shared knowledge base, every
row isolated by its scope.

The streaming route is the reference implementation of the DESIGN §6
relay pattern: typed events → SSE, with the keepalive comment and the
error frame owned by the host (this app), never the library.

Usage:
    docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:17
    export NEOSIAN_EXAMPLE_POSTGRES_DSN=postgresql://postgres:postgres@localhost/postgres
    uv run python -m neosian.schemas postgres | psql "$NEOSIAN_EXAMPLE_POSTGRES_DSN"
    uv run uvicorn examples.fastapi_chatbot:app --workers 2

    curl -N -X POST -H 'content-type: application/json' -d '{"message": "Hi!"}' \
        localhost:8000/tenants/acme/users/ada/threads/general/messages
    curl localhost:8000/tenants/acme/users/ada/threads/general
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Path as PathParam
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from neosian import (
    AgentConfig,
    Conversation,
    ErrorEvent,
    MemoryConfig,
    Model,
    Mount,
    NeosianError,
    PostgresStore,
    ReflectionConfig,
    message_to_json,
    revert_memory,
)

_DSN_ENV = "NEOSIAN_EXAMPLE_POSTGRES_DSN"
_KEEPALIVE_FRAME = ": keepalive\n\n"

# Lowercase names keep the `{tenant}--{thread}` id composition unambiguous
# and satisfy the scope grammar; hostile input becomes a 422, never a 500.
Name = Annotated[str, PathParam(pattern=r"^[a-z0-9_]{1,32}$")]

_log = logging.getLogger(__name__)


class SendBody(BaseModel):
    message: str


class UndoBody(BaseModel):
    """A `memory_write` frame's `{path, version}`, passed straight back."""

    path: str
    version: int


def _load_credentials_from_config() -> None:
    """Load API keys from ~/.neosian/config.toml if not already in environment."""
    import tomllib

    config_path = Path.home() / ".neosian" / "config.toml"
    if not config_path.exists():
        return

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    credentials = config.get("credentials", {})

    if not os.environ.get("CEREBRAS_API_KEY") and (
        cerebras_key := credentials.get("cerebras_api_key")
    ):
        os.environ["CEREBRAS_API_KEY"] = cerebras_key


def _default_configuration() -> AgentConfig:
    _load_credentials_from_config()
    return AgentConfig(
        system_prompt=(
            "You are a concise assistant for this tenant's workspace. "
            "Answer in one or two sentences."
        ),
        model=Model.CEREBRAS_GPT_OSS_120B,
        enable_todo=False,
    )


def _mounts(tenant: str, user: str) -> tuple[Mount, ...]:
    return (
        Mount(
            scope=f"tenant:{tenant}/user:{user}",
            mount_path="memories",
            description="Durable facts about this user.",
        ),
        Mount(
            scope=f"tenant:{tenant}/kb:shared",
            mount_path="kb",
            read_only=True,
            description="The tenant's shared knowledge base.",
        ),
    )


def _conversation(
    store: PostgresStore, config: AgentConfig, tenant: str, user: str, thread: str
) -> Conversation:
    """One request's view of a thread. Ids compose as `{tenant}--{thread}`
    (`:` is illegal in conversation ids); memory mounts carry the tenancy."""
    return Conversation(
        config,
        store=store,
        conversation_id=f"{tenant}--{thread}",
        mounts=_mounts(tenant, user),
        # This app builds a Conversation per HTTP request, so aclose()
        # fires per turn — not per session. Default-on reflection would
        # distill on every message; a per-request host disables the
        # rider and calls reflect() at its real session boundary (§15).
        reflection=ReflectionConfig(enabled=False),
    )


async def _sse_turn(
    convo: Conversation, message: str, *, keepalive: float
) -> AsyncIterator[str]:
    """The host-owned relay (DESIGN §6): `sse_stream` is the verbatim
    adapter; the keepalive comment and the error frame are this app's job.

    The pending `__anext__` lives in a Task that survives keepalive
    timeouts — `asyncio.wait_for(anext(events), ...)` would cancel it on
    timeout, throwing CancelledError into the agent's generator and
    killing the stream mid-turn. Cancelling the task in `finally` is the
    client-disconnect path: the abandoned turn persists nothing (§9.5).
    """
    sequence = 0
    async with convo:
        try:
            events = await convo.send(message, stream=True)
            pending = asyncio.ensure_future(anext(events))
            try:
                while True:
                    done, _ = await asyncio.wait({pending}, timeout=keepalive)
                    if not done:
                        yield _KEEPALIVE_FRAME
                        continue
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        return
                    sequence = event.sequence
                    yield event.to_sse()
                    pending = asyncio.ensure_future(anext(events))
            finally:
                pending.cancel()
        except NeosianError as exc:
            # Full text to the logs; only the machine code goes on the wire.
            _log.error("send failed: %s (%s)", convo.conversation_id, exc.code)
            yield ErrorEvent.from_exception(exc, sequence=sequence + 1).to_sse()


def create_app(
    *,
    store: PostgresStore | None = None,
    agent_config: AgentConfig | None = None,
    keepalive_seconds: float = 15.0,
) -> FastAPI:
    """Build the app. An injected store or config is caller-owned (tests);
    otherwise the app owns one store per worker process, built from
    $NEOSIAN_EXAMPLE_POSTGRES_DSN — construction is pure validation, so an
    empty DSN still boots and fails at first pool use. The schema is
    applied once by the operator (Usage block), never here: two workers
    would race the DDL.
    """
    owned = store is None
    active = (
        store
        if store is not None
        else PostgresStore(
            os.environ.get(_DSN_ENV, ""), min_size=1, max_size=8, pool_timeout=10.0
        )
    )
    config = agent_config if agent_config is not None else _default_configuration()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if owned and not os.environ.get(_DSN_ENV):
            _log.warning("%s not set — see the Usage block.", _DSN_ENV)
        if agent_config is None and not os.environ.get("CEREBRAS_API_KEY"):
            _log.warning("CEREBRAS_API_KEY not set — run `neosian configure` first.")
        yield
        if owned:
            await active.aclose()

    application = FastAPI(title="neosian multi-tenant chatbot", lifespan=lifespan)

    @application.post("/tenants/{tenant}/users/{user}/threads/{thread}/messages")
    async def send_message(
        tenant: Name, user: Name, thread: Name, body: SendBody
    ) -> StreamingResponse:
        convo = _conversation(active, config, tenant, user, thread)
        return StreamingResponse(
            _sse_turn(convo, body.message, keepalive=keepalive_seconds),
            media_type="text/event-stream",
        )

    @application.post("/tenants/{tenant}/users/{user}/threads/{thread}/memories/undo")
    async def undo_memory_write(
        tenant: Name, user: Name, thread: Name, body: UndoBody
    ) -> dict[str, Any]:
        """The undo button behind a `memory_write` frame (NP): the frame's
        `{path, version}` comes straight back, the revert appends its own
        version row (audit intact), and a document that has moved past the
        target refuses (`revert_stale`) instead of blind-restoring."""
        memory = MemoryConfig(store=active, mounts=_mounts(tenant, user))
        result = await revert_memory(
            memory,
            body.path,
            version=body.version,
            actor=f"{tenant}--{thread}#undo",
        )
        return {
            "success": result.success,
            "detail": result.data if result.success else result.error,
            "version": result.receipt.version if result.receipt else None,
        }

    @application.get("/tenants/{tenant}/users/{user}/threads/{thread}")
    async def read_thread(tenant: Name, user: Name, thread: Name) -> dict[str, Any]:
        convo = _conversation(active, config, tenant, user, thread)
        async with convo:
            started = await convo.start()
            return {
                "conversation_id": started.conversation_id,
                "messages": [message_to_json(m) for m in started.messages],
            }

    return application


app = create_app()
