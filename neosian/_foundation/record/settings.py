"""The record verb's grammar (DESIGN §20.9) — shared by the verb and its
installer.

The store half and the mount half are the memory grammar's own, so the
installer renders the layout `mcp install` renders; new here are the
foreign agent's kind and the spool. No `--actor`: the writer is the
foreign session, `<agent>:<session_id>`, by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from neosian._foundation.memory.actor import parse_actor
from neosian._foundation.memory.home import PROJECT_MOUNT_PATH, SPOOL_DIR_NAME, home
from neosian._foundation.memory.settings import (
    StoreSettings,
    add_mount_arguments,
    add_store_selection_arguments,
    resolve_mounts,
    resolve_store_selection,
)
from neosian._foundation.shared.exceptions import MemoryActorInvalidError

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping

    from neosian._foundation.memory.mounts import Mount

DEFAULT_AGENT: Final = "claude-code"
_UNUSED_ACTOR: Final = "cli:record"  # StoreSettings needs one; the session's is stamped
# Codex's `Stop` hook expects JSON on stdout at exit 0 ("plain text output is
# invalid for this event" — its hooks reference, 2026-09-03); Claude Code
# takes silence. The verb answers `{}` — an empty decision — for these kinds.
JSON_STOP_AGENTS: Final = frozenset({"codex"})


@dataclass(frozen=True, slots=True)
class RecordSettings:
    """Everything the verb needs: the store, where the sessions document
    lands (`sessions_mount`), the agent's kind, the spool."""

    store: StoreSettings
    mount: Mount
    agent: str
    spool: Path


def add_record_arguments(parser: argparse.ArgumentParser) -> None:
    add_store_selection_arguments(parser)
    add_mount_arguments(parser)
    parser.add_argument(
        "--agent",
        default=DEFAULT_AGENT,
        help="the foreign agent's actor kind — the writer is "
        f"<agent>:<session_id> (default: {DEFAULT_AGENT})",
    )
    parser.add_argument(
        "--spool",
        type=Path,
        default=None,
        help="where an open span waits between the prompt and the stop; "
        f"never the store (default: {SPOOL_DIR_NAME}/ under the home)",
    )


def validate_agent_kind(agent: str) -> None:
    """A foreign agent's kind must make `<agent>:<session_id>` an actor."""
    parse_actor(f"{agent}:session")


def sessions_mount(mounts: tuple[Mount, ...]) -> Mount | None:
    """Where the sessions document lands (§22): the mount at `/project`
    when present, else the first read-write one; None when no mount
    can take it."""
    writable = [m for m in mounts if not m.read_only and not m.edit_only]
    for mount in writable:
        if mount.mount_path == PROJECT_MOUNT_PATH:
            return mount
    return writable[0] if writable else None


def resolve_record_settings(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    env: Mapping[str, str],
    *,
    layout: Path | None = None,
) -> RecordSettings:
    """Resolve the flags against `env`; construct nothing (exit 2 via
    `parser.error` on a shape miss). `layout` is the installer's derived
    mount default (`resolve_mounts`)."""
    selection = resolve_store_selection(parser, args, env)
    mounts = resolve_mounts(parser, args, layout=layout)
    mount = sessions_mount(mounts)
    if mount is None:
        parser.error("the sessions document needs a read-write mount (no ,ro or ,eo)")
        raise AssertionError  # pragma: no cover - parser.error exits
    try:
        validate_agent_kind(args.agent)
    except MemoryActorInvalidError as exc:
        parser.error(f"--agent {args.agent!r}: {exc.reason} (a kind, e.g. claude-code)")
    store = StoreSettings(
        mounts=mounts,
        root=selection.root,
        dsn=selection.dsn,
        schema=selection.schema,
        actor=_UNUSED_ACTOR,
        url=selection.url,
        client_token=selection.client_token,
    )
    spool: Path = args.spool if args.spool is not None else home(env) / SPOOL_DIR_NAME
    return RecordSettings(store=store, mount=mount, agent=args.agent, spool=spool)
