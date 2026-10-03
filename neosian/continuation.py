"""The `neosian continue` entry point — the continue call's run tier
(DESIGN §33).

The CLI tier, like `neosian/search.py`: owns the loop, the real streams
and interrupt handling; the async engine is
`_foundation/conversation/cli_continue.py`. `python -m neosian.continuation`
is the PATH-free twin (`continue` is a keyword, so the module is not).
"""

from __future__ import annotations

import asyncio
import os
import sys

from neosian._foundation.conversation.cli_continue import run


def main(argv: list[str] | None = None, *, prog: str = "neosian continue") -> int:
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
    sys.exit(main(prog="python -m neosian.continuation"))
