"""Argument parsing for the MCP entry point — pure, I/O-free.

The grammar is the public contract (`python -m neosian.mcp --help`); the
Python surface stays internal. The store flags are the shared grammar of
`memory/settings.py` (one parser for both argv entry points, ledger
#75); Postgres arrives only through `NEOSIAN_POSTGRES_DSN` — argv is
world-readable in `ps`, so there is no `--dsn` flag (ledger #53, key
renamed at #76). Mount descriptions are model-facing prose and
deliberately absent from the grammar: hosts that want them build
`MemoryConfig` themselves and call `create_memory_server`.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path

from neosian._foundation.mcp.server import DEFAULT_ACTOR
from neosian._foundation.memory.settings import (
    POSTGRES_DSN_ENV as POSTGRES_DSN_ENV,
    StoreSettings,
    add_store_arguments,
    resolve_store_settings,
)

ServerSettings = StoreSettings

_EPILOG = (
    "Postgres: set NEOSIAN_POSTGRES_DSN instead of --root (a DSN never "
    "belongs in argv). The server never applies the schema — run "
    "`python -m neosian.schemas postgres | psql` first. Mount descriptions "
    "are not expressible here; embed via neosian.mcp.create_memory_server."
)


def parse_args(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    prog: str = "python -m neosian.mcp",
) -> ServerSettings:
    """Parse the entry point's argv against `env`; construct no store.

    Grammar errors exit 2 via argparse; scope and mount-path validation is
    structural (`Mount` raises `MemoryScopeInvalidError` /
    `MemoryPathInvalidError`, which the entry point renders). `prog` names
    the spelling actually invoked, so --help matches it.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Serve neosian memory to MCP clients on stdio.",
        epilog=_EPILOG,
    )
    add_store_arguments(parser, default_actor=DEFAULT_ACTOR)
    args = parser.parse_args(list(argv))
    return resolve_store_settings(parser, args, env, layout=Path.cwd())
