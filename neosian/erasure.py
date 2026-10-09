"""The `neosian redact` / `neosian prune` entry point: the eraser's run
tier (N8, DESIGN §38).

The CLI tier, like `neosian/mobility.py`: owns the loop, the real streams
and interrupt handling; the async engine is
`_foundation/conversation/cli_erasure.py`. `python -m neosian.erasure
redact ID --all` is the PATH-free twin (verb first, the mobility shape).
"""

from __future__ import annotations

import asyncio
import os
import sys

from neosian._foundation.conversation.cli_erasure import run


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
    sys.exit(main(prog="python -m neosian.erasure"))
