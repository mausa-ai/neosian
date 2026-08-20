"""Argument parsing for the MCP entry point — pure, I/O-free.

The grammar is the public contract (`python -m neosian.mcp --help`); the
Python surface stays internal. Postgres arrives only through
`NEOSIAN_MCP_POSTGRES_DSN` — argv is world-readable in `ps`, so there is
no `--dsn` flag (ledger #53). Mount descriptions are model-facing prose
and deliberately absent from the grammar: hosts that want them build
`MemoryConfig` themselves and call `create_memory_server`.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from neosian._foundation.memory.mounts import Mount

POSTGRES_DSN_ENV: Final = "NEOSIAN_MCP_POSTGRES_DSN"
_DEFAULT_SCHEMA: Final = "neosian"
# The mount path Anthropic's trained memory behavior roots at (§9.5.13);
# the --scope sugar mounts there, like Conversation's memory_scope=.
_SUGAR_MOUNT_PATH: Final = "memories"

_EPILOG = (
    "Postgres: set NEOSIAN_MCP_POSTGRES_DSN instead of --root (a DSN never "
    "belongs in argv). The server never applies the schema — run "
    "`python -m neosian.schemas postgres | psql` first. Mount descriptions "
    "are not expressible here; embed via neosian.mcp.create_memory_server."
)


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Everything the entry point needs to build a store and serve."""

    mounts: tuple[Mount, ...]
    root: Path | None
    dsn: str | None
    schema: str
    actor: str


def _parse_mount(parser: argparse.ArgumentParser, token: str) -> Mount:
    scope: str | None = None
    path: str | None = None
    read_only = False
    for part in token.split(","):
        key, sep, value = part.partition("=")
        if key == "ro" and not sep:
            read_only = True
        elif key == "scope" and sep and scope is None:
            scope = value
        elif key == "path" and sep and path is None:
            path = value
        else:
            parser.error(
                f"malformed --mount {token!r}: expected scope=...,path=...[,ro] "
                f"(offending part: {part!r})"
            )
    if scope is None or path is None:
        parser.error(f"--mount {token!r} needs both scope= and path=")
        raise AssertionError  # pragma: no cover - parser.error exits
    return Mount(scope=scope, mount_path=path, read_only=read_only)


def parse_args(argv: Sequence[str], env: Mapping[str, str]) -> ServerSettings:
    """Parse the entry point's argv against `env`; construct no store.

    Grammar errors exit 2 via argparse; scope and mount-path validation is
    structural (`Mount` raises `MemoryScopeInvalidError` /
    `MemoryPathInvalidError`, which the entry point renders).
    """
    parser = argparse.ArgumentParser(
        prog="python -m neosian.mcp",
        description="Serve neosian memory to MCP clients on stdio.",
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--root", type=Path, help="FileStore root directory (created on start)"
    )
    parser.add_argument(
        "--scope",
        help=f"single read-write mount of SCOPE at /{_SUGAR_MOUNT_PATH} "
        "(the memory_scope= sugar)",
    )
    parser.add_argument(
        "--mount",
        action="append",
        default=[],
        metavar="scope=...,path=...[,ro]",
        help="explicit mount; repeatable for multi-mount setups",
    )
    parser.add_argument(
        "--actor",
        default="mcp",
        help="recorded on every version row (default: mcp; e.g. mcp:claude-code)",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help=f"Postgres schema (default: {_DEFAULT_SCHEMA}; Postgres only)",
    )
    args = parser.parse_args(list(argv))

    dsn = env.get(POSTGRES_DSN_ENV) or None
    if args.root is not None and dsn is not None:
        parser.error(f"--root and {POSTGRES_DSN_ENV} are mutually exclusive")
    if args.root is None and dsn is None:
        parser.error(f"a store is required: pass --root or set {POSTGRES_DSN_ENV}")
    if args.schema is not None and args.root is not None:
        parser.error("--schema applies only to Postgres (unset --root)")

    mounts: tuple[Mount, ...]
    if args.scope is not None and args.mount:
        parser.error("--scope is the single-mount sugar; use --mount for multi-mount")
    if args.scope is not None:
        mounts = (Mount(scope=args.scope, mount_path=_SUGAR_MOUNT_PATH),)
    elif args.mount:
        mounts = tuple(_parse_mount(parser, token) for token in args.mount)
    else:
        parser.error("memory needs an explicit scope: pass --scope or --mount")
        raise AssertionError  # pragma: no cover - parser.error exits

    return ServerSettings(
        mounts=mounts,
        root=args.root,
        dsn=dsn,
        schema=args.schema if args.schema is not None else _DEFAULT_SCHEMA,
        actor=args.actor,
    )
