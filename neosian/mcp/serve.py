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
from pathlib import Path

from neosian._foundation.mcp.server import create_memory_server, serve_stdio
from neosian._foundation.mcp.settings import ServerSettings, parse_args
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.shared.exceptions import MemoryStoreError


async def _run(settings: ServerSettings) -> None:
    # One root/DSN/URL branch for every entry (NL: `--url` puts this
    # server behind the state process — the multi-writer shape, §8).
    async with open_store(settings) as store:
        server = await create_memory_server(
            MemoryConfig(store=store, mounts=settings.mounts),
            actor=settings.actor,
        )
        await serve_stdio(server)


def main(argv: list[str] | None = None, *, prog: str = "neosian mcp") -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "install":
        # One literal first token, routed before the server grammar: it
        # stays flat (subparsers would rename the documented
        # `python -m neosian.mcp --root ...` invocation for no gain).
        from neosian._foundation.mcp.install import Environment, run_install

        try:
            return run_install(
                args[1:],
                os.environ,
                context=Environment(
                    home=Path.home(),
                    cwd=Path.cwd(),
                    platform=sys.platform,
                    env=os.environ,
                    executable=sys.executable,
                ),
                out=sys.stdout,
                err=sys.stderr,
                prog=f"{prog} install",
            )
        except KeyboardInterrupt:
            return 130
    try:
        settings = parse_args(args, os.environ, prog=prog)
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
