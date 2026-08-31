"""The `http` transport — the store wire under the model (#113).

An http cell's memory tool is the standard function tool over a
`MemoryConfig` whose store is a `RemoteStore`: every command's store
I/O — and the session's index render, reflection, and maintenance —
crosses the twelve-route wire (codec, §5 error round-trip) against an
in-process state process (`build_app` on `httpx.ASGITransport`),
keyless and port-free. The model-visible surface is identical on every
transport (#50), so the cells measure the owned wire under live
traffic, never the MCP SDK's transport — that door stays
deterministically pinned by the parity table and the five-transport
test, and an MCP-client transport waits for NC1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount

_TOKEN = "eval-http"
_BASE_URL = "http://state-process"


@asynccontextmanager
async def open_http_memory(
    *, store_root: Path, mounts: tuple[Mount, ...]
) -> AsyncIterator[MemoryConfig]:
    """A `MemoryConfig` whose store I/O crosses the wire.

    The server imports are lazy: `build_app` pulls the guarded `server`
    extra, and `neosian.evaluation` must stay importable without it —
    an http cell without the extra fails with the install hint, never
    the facade's import.
    """
    from neosian._foundation.server.app import build_app
    from neosian._foundation.server.remote import RemoteStore

    app = await build_app(FileStore(store_root), token=_TOKEN)
    remote = await RemoteStore.connect(
        _BASE_URL, token=_TOKEN, transport=httpx.ASGITransport(app=app)
    )
    try:
        yield MemoryConfig(store=remote, mounts=mounts)
    finally:
        await remote.aclose()
