"""Argument parsing for `neosian serve` — pure, I/O-free.

The store flags are the shared grammar of `memory/settings.py` (ledger
#75) with `default_actor="serve"`; the mounts default like every shell
verb's (§30: `NEOSIAN_SCOPE`, else the working directory's project
layout) and gate the `/mcp` surface — the store-shaped API needs none
(the §18 relaxation stands for a directory with no derived name).

The bearer tokens arrive only through `NEOSIAN_SERVE_TOKEN` (one token,
or `actor=token[,…]` per client — DESIGN §20) — argv is
world-readable in `ps`, the ledger #53 rule that keeps DSNs off command
lines applies to tokens identically. Unset refuses to start at the
grammar tier: default-deny is code, not configuration (the #107 shape) —
the process never serves unauthenticated.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from neosian._foundation.memory.settings import (
    POSTGRES_DSN_ENV as POSTGRES_DSN_ENV,
    StoreSettings,
    add_store_arguments,
    resolve_mounts,
    resolve_store_selection,
)
from neosian._foundation.server.tokens import parse_clients
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SERVE_TOKEN_ENV: Final = "NEOSIAN_SERVE_TOKEN"
# The `/mcp` surface's process-wide actor (DESIGN §18.6, §20): the SDK's
# session manager gives no per-request identity, so one name per process.
DEFAULT_ACTOR: Final = "serve:mcp"
DEFAULT_HOST: Final = "127.0.0.1"
# "NEOS" on a phone keypad; unassigned in the registered range.
DEFAULT_PORT: Final = 6367

_EPILOG = (
    f"Auth: set {SERVE_TOKEN_ENV} (a token never belongs in argv); the "
    "server refuses to start without it. Postgres: set "
    f"{POSTGRES_DSN_ENV} instead of --root. Mounts (--scope/--mount, "
    "default: this directory's project layout) gate the /mcp surface; the "
    "store API needs none."
)


@dataclass(frozen=True, slots=True)
class ServeSettings:
    """Everything the runner needs: the store, the bind, the token table
    (the raw `NEOSIAN_SERVE_TOKEN` value; `build_app` parses it)."""

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
    add_store_arguments(parser, default_actor=DEFAULT_ACTOR)
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})"
    )
    args = parser.parse_args(list(argv))
    if not 1 <= args.port <= 65535:
        parser.error(f"--port must be in 1..65535, got {args.port}")
    selection = resolve_store_selection(parser, args, env)
    if selection.url is not None:
        parser.error(
            "--url is a client's flag: the state process serves a root or a DSN"
        )
    mounts = resolve_mounts(parser, args, env, required=False, layout=Path.cwd())
    token = env.get(SERVE_TOKEN_ENV) or ""
    if not token:
        parser.error(
            f"{SERVE_TOKEN_ENV} is required — the state process never "
            "serves unauthenticated"
        )
    try:
        parse_clients(token)  # the table's shape is grammar: exit 2
    except ConfigurationError as exc:
        parser.error(exc.message)
    return ServeSettings(
        store=StoreSettings(
            mounts=mounts,
            root=selection.root,
            dsn=selection.dsn,
            schema=selection.schema,
            actor=args.actor,
        ),
        host=args.host,
        port=args.port,
        token=token,
    )
