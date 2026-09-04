"""Store selection and mount grammar shared by the argv entry points.

One grammar for every command that builds a store from flags —
`python -m neosian.mcp` and `neosian memory` (extracted at NA, ledger
#75, the #50 dispatch precedent one level up). Pure and I/O-free:
nothing here constructs a store or touches the filesystem. Postgres
arrives only through `NEOSIAN_POSTGRES_DSN` — argv is world-readable in
`ps`, so there is no `--dsn` flag (ledger #53; the key renamed from
`NEOSIAN_MCP_POSTGRES_DSN` when it stopped being MCP-specific, #76).
Since NU the home (`home.py`, DESIGN §22) is the store when no flag names
one — a stated location, still constructed only past the grammar tier.
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

from neosian._foundation.memory.actor import parse_actor
from neosian._foundation.memory.home import HOME_ENV, home, project_mounts
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    MemoryActorInvalidError,
)

POSTGRES_DSN_ENV: Final = "NEOSIAN_POSTGRES_DSN"
# The token a *client* of the state process presents (DESIGN §20) — a
# distinct key from the server's table, because one laptop runs both.
CLIENT_TOKEN_ENV: Final = "NEOSIAN_CLIENT_TOKEN"
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
    # The daemon URL (NL): the third store, beside a root and a DSN, with
    # the client's token from the environment — never argv (#53).
    url: str | None = None
    client_token: str | None = None


def parse_mount(parser: argparse.ArgumentParser, token: str) -> Mount:
    scope: str | None = None
    path: str | None = None
    read_only = False
    edit_only = False
    for part in token.split(","):
        key, sep, value = part.partition("=")
        if key == "ro" and not sep and not edit_only:
            read_only = True
        elif key == "eo" and not sep and not read_only:
            edit_only = True
        elif key == "scope" and sep and scope is None:
            scope = value
        elif key == "path" and sep and path is None:
            path = value
        else:
            parser.error(
                f"malformed --mount {token!r}: expected scope=...,path=...[,ro|,eo] "
                f"(offending part: {part!r})"
            )
    if scope is None or path is None:
        parser.error(f"--mount {token!r} needs both scope= and path=")
        raise AssertionError  # pragma: no cover - parser.error exits
    return Mount(scope=scope, mount_path=path, read_only=read_only, edit_only=edit_only)


def format_mount(mount: Mount) -> str:
    """The inverse of `parse_mount` — a token it round-trips.

    Mount descriptions are not expressible in the token grammar
    (deliberately — they are model-facing prose) and are dropped.
    """
    token = f"scope={mount.scope},path={mount.mount_path}"
    if mount.read_only:
        return f"{token},ro"
    if mount.edit_only:
        return f"{token},eo"
    return token


def add_store_selection_arguments(parser: argparse.ArgumentParser) -> None:
    """The store-selection half of the grammar: a root, a daemon URL, or
    (by environment) a DSN; `resolve_store_selection` reads them."""
    parser.add_argument(
        "--root",
        type=Path,
        help="FileStore root directory (created on start; default: the home, "
        f"~/.neosian or ${HOME_ENV})",
    )
    parser.add_argument(
        "--url",
        help="the state process to speak to (http[s]://host:port); the "
        f"client token comes from {CLIENT_TOKEN_ENV}",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help=f"Postgres schema (default: {DEFAULT_SCHEMA}; Postgres only)",
    )


def add_mount_arguments(parser: argparse.ArgumentParser) -> None:
    """The mount half of the grammar: the `--scope` sugar or repeatable
    `--mount`; `resolve_mounts` reads them."""
    parser.add_argument(
        "--scope",
        help=f"single read-write mount of SCOPE at /{SUGAR_MOUNT_PATH} "
        "(the memory_scope= sugar)",
    )
    parser.add_argument(
        "--mount",
        action="append",
        default=[],
        metavar="scope=...,path=...[,ro|,eo]",
        help="explicit mount; repeatable for multi-mount setups "
        "(ro = read-only, eo = edit-only: existing documents only)",
    )


def add_store_arguments(parser: argparse.ArgumentParser, *, default_actor: str) -> None:
    """Add the store flags to `parser`; `resolve_store_settings` reads them."""
    add_store_selection_arguments(parser)
    add_mount_arguments(parser)
    parser.add_argument(
        "--actor",
        default=default_actor,
        help="who writes: <kind>:<id>[/...] — recorded on every row "
        f"(default: {default_actor})",
    )


@dataclass(frozen=True, slots=True)
class StoreSelection:
    """Which store the flags name — exactly one of root, dsn, url; the
    home when none is named."""

    root: Path | None
    dsn: str | None
    url: str | None
    client_token: str | None
    schema: str


def resolve_store_selection(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    env: Mapping[str, str],
) -> StoreSelection:
    """Resolve the store half of the grammar: at most one of root, DSN,
    URL — the home (§22) when none is named. Explicit flags always win."""
    dsn = env.get(POSTGRES_DSN_ENV) or None
    url: str | None = args.url
    named = [
        name
        for name, present in (
            ("--root", args.root is not None),
            (POSTGRES_DSN_ENV, dsn is not None),
            ("--url", url is not None),
        )
        if present
    ]
    if len(named) > 1:
        parser.error(f"{' and '.join(named)} are mutually exclusive")
    root: Path | None = args.root if named else home(env)
    if args.schema is not None and dsn is None:
        parser.error("--schema applies only to Postgres (unset --root/--url)")
    client_token: str | None = None
    if url is not None:
        if not url.startswith(("http://", "https://")):
            parser.error(f"--url must start with http:// or https://, got {url!r}")
        client_token = env.get(CLIENT_TOKEN_ENV) or None
        if client_token is None:
            parser.error(
                f"--url needs {CLIENT_TOKEN_ENV} — the client's bearer token, "
                "never on the command line"
            )
    schema: str = args.schema if args.schema is not None else DEFAULT_SCHEMA
    return StoreSelection(
        root=root, dsn=dsn, url=url, client_token=client_token, schema=schema
    )


def resolve_mounts(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    required: bool = True,
    layout: Path | None = None,
) -> tuple[Mount, ...]:
    """Resolve the mount half of the grammar.

    `required=False` is the state process's relaxation (DESIGN §18): its
    store-shaped API needs no mounts — only the MCP surface does.
    `layout` is the installers' default (§22): the directory whose
    project layout is rendered — visibly, into the client's config —
    when no flag names a mount. Everywhere else the scope stays the
    caller's to spell; the refusal shows this directory's spelling.
    """
    if args.scope is not None and args.mount:
        parser.error("--scope is the single-mount sugar; use --mount for multi-mount")
    if args.scope is not None:
        return (Mount(scope=args.scope, mount_path=SUGAR_MOUNT_PATH),)
    if args.mount:
        return tuple(parse_mount(parser, token) for token in args.mount)
    if layout is not None:
        try:
            return project_mounts(layout)
        except ConfigurationError as exc:
            parser.error(exc.message)
    if required:
        parser.error(
            f"memory needs an explicit scope: pass --scope or --mount{layout_hint()}"
        )
        raise AssertionError  # pragma: no cover - parser.error exits
    return ()


def layout_hint() -> str:
    """This directory's project layout as `--mount` tokens, for a refusal;
    empty where the directory has no derived scope."""
    try:
        mounts = project_mounts()
    except ConfigurationError:
        return ""
    tokens = " ".join(f"--mount {format_mount(m)}" for m in mounts)
    return f" (this directory's layout: {tokens})"


def resolve_store_settings(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    env: Mapping[str, str],
    *,
    layout: Path | None = None,
) -> StoreSettings:
    """Resolve parsed store flags against `env`; construct no store.

    Shape errors exit 2 via `parser.error`; scope and mount-path
    validation is structural (`Mount` raises `MemoryScopeInvalidError` /
    `MemoryPathInvalidError`, which the entry point renders). `layout`
    is `resolve_mounts`'s: the installers' derived default.
    """
    selection = resolve_store_selection(parser, args, env)
    mounts = resolve_mounts(parser, args, layout=layout)
    return StoreSettings(
        mounts=mounts,
        root=selection.root,
        dsn=selection.dsn,
        schema=selection.schema,
        actor=resolve_actor(parser, args.actor),
        url=selection.url,
        client_token=selection.client_token,
    )


def resolve_actor(parser: argparse.ArgumentParser, actor: str) -> str:
    """Grammar-tier actor validation (DESIGN §20): exit 2, nothing built."""
    try:
        return str(parse_actor(actor))
    except MemoryActorInvalidError as exc:
        parser.error(f"--actor {actor!r}: {exc.reason} (e.g. cli:claude-code)")
        raise AssertionError from None  # pragma: no cover - parser.error exits
