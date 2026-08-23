"""Argument parsing for `neosian serve` — pure, I/O-free.

The store flags are the shared grammar of `memory/settings.py` (ledger
#75) with `default_actor="serve"`; mounts are optional here — the §18
relaxation: the store-shaped API needs no mounts, only the MCP surface
does, and a RemoteStore-only deployment has no natural scope to mount.

The bearer token arrives only through `NEOSIAN_SERVE_TOKEN` — argv is
world-readable in `ps`, the ledger #53 rule that keeps DSNs off command
lines applies to tokens identically. Unset refuses to start at the
grammar tier: default-deny is code, not configuration (the #107 shape) —
the process never serves unauthenticated.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from neosian._foundation.memory.settings import (
    POSTGRES_DSN_ENV as POSTGRES_DSN_ENV,
)
from neosian._foundation.memory.settings import (
    StoreSettings,
    add_store_arguments,
    resolve_mounts,
    resolve_store_selection,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SERVE_TOKEN_ENV: Final = "NEOSIAN_SERVE_TOKEN"
DEFAULT_HOST: Final = "127.0.0.1"
# "NEOS" on a phone keypad; unassigned in the registered range.
DEFAULT_PORT: Final = 6367

_EPILOG = (
    f"Auth: set {SERVE_TOKEN_ENV} (a token never belongs in argv); the "
    "server refuses to start without it. Postgres: set "
    f"{POSTGRES_DSN_ENV} instead of --root. Mounts (--scope/--mount) are "
    "optional and gate the /mcp surface; the store API needs none."
)


@dataclass(frozen=True, slots=True)
class ServeSettings:
    """Everything the runner needs: the store, the bind, the token."""

    store: StoreSettings
    host: str
    port: int
    token: str


def parse_args(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    prog: str = "neosian serve",
) -> ServeSettings:
    """Parse the entry point's argv against `env`; construct no store.

    Grammar errors — a missing token included — exit 2 via argparse;
    scope and mount-path validation is structural (`Mount` raises, the
    entry point renders).
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Serve neosian memory and conversations over HTTP — "
        "the state process.",
        epilog=_EPILOG,
    )
    add_store_arguments(parser, default_actor="serve")
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})"
    )
    args = parser.parse_args(list(argv))
    if not 1 <= args.port <= 65535:
        parser.error(f"--port must be in 1..65535, got {args.port}")
    root, dsn, schema = resolve_store_selection(parser, args, env)
    mounts = resolve_mounts(parser, args, required=False)
    token = env.get(SERVE_TOKEN_ENV) or ""
    if not token:
        parser.error(
            f"{SERVE_TOKEN_ENV} is required — the state process never "
            "serves unauthenticated"
        )
    return ServeSettings(
        store=StoreSettings(
            mounts=mounts, root=root, dsn=dsn, schema=schema, actor=args.actor
        ),
        host=args.host,
        port=args.port,
        token=token,
    )
