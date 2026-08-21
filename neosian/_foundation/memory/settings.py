"""Store selection and mount grammar shared by the argv entry points.

One grammar for every command that builds a store from flags —
`python -m neosian.mcp` and `neosian memory` (extracted at NA, ledger
#75, the #50 dispatch precedent one level up). Pure and I/O-free:
nothing here constructs a store or touches the filesystem. Postgres
arrives only through `NEOSIAN_POSTGRES_DSN` — argv is world-readable in
`ps`, so there is no `--dsn` flag (ledger #53; the key renamed from
`NEOSIAN_MCP_POSTGRES_DSN` when it stopped being MCP-specific, #76).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TextIO

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

from neosian._foundation.memory.mounts import Mount

POSTGRES_DSN_ENV: Final = "NEOSIAN_POSTGRES_DSN"
DEFAULT_SCHEMA: Final = "neosian"
# The mount path Anthropic's trained memory behavior roots at (§9.5.13);
# the --scope sugar mounts there, like Conversation's memory_scope=.
SUGAR_MOUNT_PATH: Final = "memories"


class StreamParser(argparse.ArgumentParser):
    """argparse over injected streams.

    The argv engines run in-process (the eval harness, unit suites), so
    they must not write to — or swap — the process's real stdio; help
    and errors go to the streams `bind` sets.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.out: TextIO = sys.stdout
        self.err: TextIO = sys.stderr

    def bind(self, out: TextIO, err: TextIO) -> None:
        self.out = out
        self.err = err

    def _print_message(
        self, message: str, file: SupportsWrite[str] | None = None
    ) -> None:
        if message:
            target = self.err if file is sys.stderr else self.out
            target.write(message)


@dataclass(frozen=True, slots=True)
class StoreSettings:
    """Everything an entry point needs to build a store."""

    mounts: tuple[Mount, ...]
    root: Path | None
    dsn: str | None
    schema: str
    actor: str


def parse_mount(parser: argparse.ArgumentParser, token: str) -> Mount:
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


def format_mount(mount: Mount) -> str:
    """The inverse of `parse_mount` — a token it round-trips.

    Mount descriptions are not expressible in the token grammar
    (deliberately — they are model-facing prose) and are dropped.
    """
    token = f"scope={mount.scope},path={mount.mount_path}"
    return f"{token},ro" if mount.read_only else token


def add_store_arguments(parser: argparse.ArgumentParser, *, default_actor: str) -> None:
    """Add the store flags to `parser`; `resolve_store_settings` reads them."""
    parser.add_argument(
        "--root", type=Path, help="FileStore root directory (created on start)"
    )
    parser.add_argument(
        "--scope",
        help=f"single read-write mount of SCOPE at /{SUGAR_MOUNT_PATH} "
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
        default=default_actor,
        help="recorded on every version row "
        f"(default: {default_actor}; e.g. {default_actor}:claude-code)",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help=f"Postgres schema (default: {DEFAULT_SCHEMA}; Postgres only)",
    )


def resolve_store_settings(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    env: Mapping[str, str],
) -> StoreSettings:
    """Resolve parsed store flags against `env`; construct no store.

    Shape errors exit 2 via `parser.error`; scope and mount-path
    validation is structural (`Mount` raises `MemoryScopeInvalidError` /
    `MemoryPathInvalidError`, which the entry point renders).
    """
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
        mounts = (Mount(scope=args.scope, mount_path=SUGAR_MOUNT_PATH),)
    elif args.mount:
        mounts = tuple(parse_mount(parser, token) for token in args.mount)
    else:
        parser.error("memory needs an explicit scope: pass --scope or --mount")
        raise AssertionError  # pragma: no cover - parser.error exits

    return StoreSettings(
        mounts=mounts,
        root=args.root,
        dsn=dsn,
        schema=args.schema if args.schema is not None else DEFAULT_SCHEMA,
        actor=args.actor,
    )
