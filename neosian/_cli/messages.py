"""Shell entry; library mailbox operations remain async-only."""

import asyncio
import os
import sys

from neosian._foundation.messaging.cli import run


def main(argv: list[str]) -> int:
    try:
        return asyncio.run(run(argv, os.environ, out=sys.stdout, err=sys.stderr))
    except KeyboardInterrupt:
        return 130
