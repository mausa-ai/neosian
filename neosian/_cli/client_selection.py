"""Invocation-only client locations and discovery reports."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from neosian._foundation.mcp.targets import resolve_target
from neosian._foundation.memory.settings import StreamParser
from neosian._foundation.shared.client_config import Environment


def add_locations(parser: StreamParser) -> None:
    parser.add_argument(
        "--at",
        action="append",
        default=[],
        metavar="CLIENT=DIR",
        help="client config directory for this invocation only (repeatable)",
    )


def with_locations(
    parser: StreamParser,
    context: Environment,
    values: Sequence[str],
    clients: Sequence[str],
) -> Environment:
    directories = dict(context.client_dirs)
    env = dict(context.env)
    for value in values:
        client, separator, directory = value.partition("=")
        if not separator or client not in clients or not directory.strip():
            parser.error(
                "--at requires CLIENT=DIR with a supported client and directory"
            )
        if client in directories:
            parser.error(f"--at repeats {client}")
        path = Path(directory).expanduser()
        directories[client] = (context.cwd / path).resolve()
        # The two CLI-owned registrations must use the same location as
        # the file installers; no ambient environment is modified.
        variable = {"claude-code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}.get(
            client
        )
        if variable:
            env[variable] = str(directories[client])
    return replace(context, client_dirs=directories, env=env)


def discovery(context: Environment, clients: Sequence[str]) -> list[dict[str, object]]:
    return [
        {
            "client": client,
            "directory": str(resolve_target(client, context).evidence_dir),
            "installed": resolve_target(client, context).evidence_dir.is_dir(),
        }
        for client in clients
    ]


def discovery_text(rows: Sequence[dict[str, object]]) -> str:
    return "".join(
        f"  {row['client']}: not found; searched {row['directory']}\n"
        for row in rows
        if not row["installed"]
    )
