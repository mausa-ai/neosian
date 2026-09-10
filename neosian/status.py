"""The `neosian status` entry point — the console's run tier (DESIGN §30).

The CLI tier, like `neosian/ledger.py`: owns the loop, the real streams,
the ambient `Environment` and interrupt handling; the engine is
`_cli/status.py`. `python -m neosian.status` is the PATH-free twin.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from neosian._cli.status import run
from neosian._foundation.shared.client_config import Environment


def main(argv: list[str] | None = None, *, prog: str = "neosian status") -> int:
    try:
        return asyncio.run(
            run(
                sys.argv[1:] if argv is None else argv,
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
                prog=prog,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover - the twin
    sys.exit(main(prog="python -m neosian.status"))
