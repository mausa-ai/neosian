"""The `neosian record` entry point — the record's run tier (DESIGN §20.9).

The CLI tier, like `neosian/audit.py`: owns the loop, the real streams
and interrupt handling; the async engine is `_foundation/record/cli.py`,
the installer `_foundation/record/install.py` — routed on the literal
first token, as `neosian mcp install` is. `python -m neosian.record` is
the PATH-free twin, the one a hook line names.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from neosian._foundation.record.cli import run


def main(argv: list[str] | None = None, *, prog: str = "neosian record") -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args and args[0] == "install":
            from neosian._foundation.record.install import run_install
            from neosian._foundation.shared.client_config import Environment

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
        return asyncio.run(
            run(
                args,
                os.environ,
                stdin=sys.stdin,
                out=sys.stdout,
                err=sys.stderr,
                prog=prog,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover - the twin
    sys.exit(main(prog="python -m neosian.record"))
