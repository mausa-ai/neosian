"""The `neosian memory` entry point — the shell transport's run tier.

Library code stays async-only (`asyncio.run` deadlocks in notebooks and
servers); this module is the CLI tier, like `neosian/mcp/serve.py`. The
engine — grammar, store lifetime, rendering — is
`_foundation/memory/cli.py`; the eval harness calls that async engine
in-process from its running loop (DESIGN §14), while this wrapper owns
the loop, the real streams and interrupt handling.
"""

from __future__ import annotations

import asyncio
import os
import sys

from neosian._foundation.memory.cli import run


def main(argv: list[str] | None = None, *, prog: str = "neosian memory") -> int:
    try:
        return asyncio.run(
            run(
                sys.argv[1:] if argv is None else argv,
                os.environ,
                stdin=sys.stdin,
                out=sys.stdout,
                err=sys.stderr,
                prog=prog,
            )
        )
    except ImportError as exc:
        # The missing-extra hint, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
