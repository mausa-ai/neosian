"""The uvicorn runner — store lifetime in, graceful shutdown out.

`open_store` owns the DSN-vs-root branch and the close (the ledger #33
rule at process scale); uvicorn's own signal handling makes SIGTERM the
graceful path — in-flight requests drain, then `serve()` returns and the
store closes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.server.app import build_app
from neosian._foundation.server.sdk import uvicorn

if TYPE_CHECKING:
    from neosian._foundation.server.settings import ServeSettings


async def run_server(settings: ServeSettings) -> None:
    """Serve until stopped; the store is closed on the way out."""
    async with open_store(settings.store) as store:
        app = await build_app(
            store,
            token=settings.token,
            mounts=settings.store.mounts,
            actor=settings.store.actor,
        )
        config = uvicorn.Config(
            app, host=settings.host, port=settings.port, log_level="info"
        )
        await uvicorn.Server(config).serve()
