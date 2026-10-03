"""The mailbox CLI shares the agent tool, store grammar and exit tiers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

import httpx

from neosian._foundation.memory.cli import _render
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.settings import (
    StreamParser,
    add_store_arguments,
    resolve_store_settings,
)
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.messaging.tools import COMMANDS, create_messages_tool
from neosian._foundation.shared.exceptions import NeosianError
from neosian._foundation.tools.base import ToolResult


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    out: TextIO,
    err: TextIO,
) -> int:
    parser = StreamParser(
        prog="neosian messages", description="Durable messages and reminders."
    )
    parser.bind(out, err)
    parser.add_argument("command", choices=COMMANDS)
    add_store_arguments(parser, default_actor="cli:local")
    parser.add_argument(
        "--target",
        help="destination scope or mount (default: project, then first mount)",
    )
    for option in (
        "session",
        "message-id",
        "body",
        "conversation",
        "due-at",
        "about",
        "token",
        "outcome",
    ):
        parser.add_argument("--" + option)
    for option in ("turn", "delay-seconds", "about-turn", "occurrence"):
        parser.add_argument("--" + option, type=int)
    parser.add_argument(
        "--delivery", choices=("next_activity", "wake"), default="next_activity"
    )
    parser.add_argument(
        "--status",
        choices=("unread", "open", "scheduled", "closed", "all"),
        default="unread",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--actionable", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    try:
        args = parser.parse_args(list(argv))
        settings = resolve_store_settings(parser, args, env, layout=Path.cwd())
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        async with open_store(settings) as store:
            config = MemoryConfig(store=store, mounts=settings.mounts)
            tool = create_messages_tool(config, actor=settings.actor or "cli:local")
            target = args.target
            if target is None and args.command != "list":
                target = next(
                    (m.scope for m in settings.mounts if m.mount_path == "project"),
                    settings.mounts[0].scope,
                )
            arguments = {
                key: getattr(args, key)
                for key in (
                    "command",
                    "session",
                    "message_id",
                    "body",
                    "conversation",
                    "turn",
                    "delivery",
                    "actionable",
                    "due_at",
                    "delay_seconds",
                    "about",
                    "about_turn",
                    "occurrence",
                    "token",
                    "outcome",
                    "status",
                    "limit",
                )
            }
            result = await tool(scope=target, **arguments)
    except (NeosianError, ValueError, OSError, httpx.HTTPError) as exc:
        result = ToolResult.fail(str(exc))
    return _render(result, json_output=args.json_output, out=out, err=err)
