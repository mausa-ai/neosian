"""The stdio entry point — the one place this package runs an event loop.

Library code stays async-only (`asyncio.run` deadlocks in notebooks and
servers); this module is the CLI tier, like `neosian/schemas.py`. It owns
the store's lifetime: built here, closed here (ledger #33's rule applied
to the MCP process). Nothing prints to stdout — stdout is the MCP wire.
"""

from __future__ import annotations

import asyncio
import os
import sys

from neosian._foundation.mcp.server import create_memory_server, serve_stdio
from neosian._foundation.mcp.settings import ServerSettings, parse_args
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.postgres.store import PostgresStore
from neosian._foundation.shared.exceptions import MemoryStoreError


def _build_store(settings: ServerSettings) -> MemoryStore:
    if settings.dsn is not None:
        return PostgresStore(settings.dsn, schema=settings.schema)
    assert settings.root is not None  # parse_args guarantees one of the two
    return FileStore(settings.root)


async def _run(settings: ServerSettings) -> None:
    store = _build_store(settings)
    try:
        server = await create_memory_server(
            MemoryConfig(store=store, mounts=settings.mounts),
            actor=settings.actor,
        )
        await serve_stdio(server)
    finally:
        if isinstance(store, PostgresStore):
            await store.aclose()


def main(argv: list[str] | None = None) -> int:
    try:
        settings = parse_args(sys.argv[1:] if argv is None else argv, os.environ)
    except MemoryStoreError as exc:
        print(f"error: [{exc.code}] {exc.message}", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(settings))
    except ImportError as exc:
        # The missing-extra hint, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
