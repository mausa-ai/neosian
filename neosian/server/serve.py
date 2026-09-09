"""The HTTP entry point — the one place this package runs an event loop.

Library code stays async-only; this module is the CLI tier, like
`neosian/mcp/serve.py`. The runner import is lazy so the grammar tier —
--help, exit-2 errors, the missing-token refusal — works without the
serving stack; a missing stack surfaces as the reinstall hint at exit 1.
"""

from __future__ import annotations

import asyncio
import os
import sys

from neosian._foundation.server.settings import ServeSettings, parse_args
from neosian._foundation.shared.exceptions import MemoryStoreError


async def _serve(settings: ServeSettings) -> None:
    from neosian._foundation.server.runner import run_server

    await run_server(settings)


def main(argv: list[str] | None = None, *, prog: str = "neosian serve") -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        settings = parse_args(args, os.environ, prog=prog)
    except MemoryStoreError as exc:
        print(f"error: [{exc.code}] {exc.message}", file=sys.stderr)
        return 2
    try:
        asyncio.run(_serve(settings))
    except ImportError as exc:
        # The reinstall hint, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
