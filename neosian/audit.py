"""The `neosian audit` entry point — the ledger's run tier (DESIGN §20).

The CLI tier, like `neosian/memory/cli.py`: owns the loop, the real
streams and interrupt handling; the async engine is
`_foundation/memory/cli_audit.py`. `python -m neosian.audit` is the
PATH-free twin.
"""

from __future__ import annotations

import asyncio
import os
import sys

from neosian._foundation.memory.cli_audit import run


def main(argv: list[str] | None = None, *, prog: str = "neosian audit") -> int:
    try:
        return asyncio.run(
            run(
                sys.argv[1:] if argv is None else argv,
                os.environ,
                out=sys.stdout,
                err=sys.stderr,
                prog=prog,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover - the twin
    sys.exit(main(prog="python -m neosian.audit"))
