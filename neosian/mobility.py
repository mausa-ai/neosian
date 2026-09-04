"""The `neosian export` / `neosian import` entry point — store mobility's
run tier (DESIGN §26).

The CLI tier, like `neosian/audit.py`: owns the loop, the real streams
and interrupt handling; the async engine is
`_foundation/memory/cli_transfer.py`. `python -m neosian.mobility export
DIR` is the PATH-free twin (verb first — `import` cannot be a module).
"""

from __future__ import annotations

import asyncio
import os
import sys

from neosian._foundation.memory.cli_transfer import run


def main(argv: list[str] | None = None, *, prog: str = "neosian") -> int:
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
    sys.exit(main(prog="python -m neosian.mobility"))
