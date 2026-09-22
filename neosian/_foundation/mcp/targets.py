"""The MCP clients' registration surfaces: one row per client (DESIGN
§14.5, §22.6).

A row names the file a client reads its servers from at a level, the
directory that proves the client is installed, and who writes the file:
neosian merges most of them, but Claude Code's user-level `~/.claude.json`
and Codex's TOML belong to the client's own CLI (`cli`). Three clients
keep one file for every project, so they have the user level alone.
Never imports the MCP SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from neosian._foundation.shared.client_config import (
    Environment,
    claude_home,
    codex_home,
    opencode_config_dir,
    read_document,
)
from neosian._foundation.shared.muse_config import config_dir as muse_config_dir

SERVER_NAME: Final = "neosian-memory"
_SERVERS_KEY: Final = "mcpServers"


@dataclass(frozen=True, slots=True)
class ClientTarget:
    """One client's registration surface — a row, so reversals are cheap."""

    client: str
    label: str
    level: str  # the level this row is: a one-file client is always "user"
    config_path: Path
    evidence_dir: Path  # must already exist; NEVER created
    servers_key: str
    scope_note: str
    # The client's own CLI writes this file (Claude Code's user level,
    # Codex's TOML): print-only here, --write refused with its command.
    cli: str | None = None
    # The entry's shape: `mcpServers` ({command, args}) or OpenCode's
    # ({type: local, command: [...], enabled}).
    style: str = "mcpServers"


def _claude_code(context: Environment, level: str) -> ClientTarget:
    if level == "project":
        return ClientTarget(
            client="claude-code",
            label="Claude Code",
            level=level,
            config_path=context.cwd / ".mcp.json",
            evidence_dir=claude_home(context),
            servers_key=_SERVERS_KEY,
            scope_note="project level: .mcp.json travels with this directory's repo",
        )
    override = context.env.get("CLAUDE_CONFIG_DIR")
    return ClientTarget(
        client="claude-code",
        label="Claude Code",
        level=level,
        config_path=(Path(override) if override else context.home) / ".claude.json",
        evidence_dir=claude_home(context),
        servers_key=_SERVERS_KEY,
        scope_note="user level: every Claude Code project on this machine; "
        "Claude Code's own CLI writes this file",
        cli="claude",
    )


def _claude_desktop(context: Environment, _level: str) -> ClientTarget:
    if context.platform == "darwin":
        base = context.home / "Library" / "Application Support" / "Claude"
    elif context.platform == "win32":
        appdata = context.env.get("APPDATA")
        roaming = Path(appdata) if appdata else context.home / "AppData" / "Roaming"
        base = roaming / "Claude"
    else:
        base = context.home / ".config" / "Claude"
    return ClientTarget(
        client="claude-desktop",
        label="Claude Desktop",
        level="user",
        config_path=base / "claude_desktop_config.json",
        evidence_dir=base,
        servers_key=_SERVERS_KEY,
        scope_note="user level: every Claude Desktop conversation",
    )


def _cursor(context: Environment, _level: str) -> ClientTarget:
    base = context.home / ".cursor"
    return ClientTarget(
        client="cursor",
        label="Cursor",
        level="user",
        config_path=base / "mcp.json",
        evidence_dir=base,
        servers_key=_SERVERS_KEY,
        scope_note="user level: every Cursor project on this machine",
    )


def _codex(context: Environment, _level: str) -> ClientTarget:
    base = codex_home(context)
    return ClientTarget(
        client="codex",
        label="Codex",
        level="user",
        config_path=base / "config.toml",
        evidence_dir=base,
        servers_key="mcp_servers",
        scope_note="user level: every Codex project on this machine; Codex's "
        "own CLI writes its TOML",
        cli="codex",
    )


def _opencode(context: Environment, level: str) -> ClientTarget:
    base = opencode_config_dir(context)
    where = context.cwd if level == "project" else base
    note = (
        "project level: opencode.json travels with this directory's repo"
        if level == "project"
        else "user level: every OpenCode project on this machine"
    )
    return ClientTarget(
        client="opencode",
        label="OpenCode",
        level=level,
        config_path=where / "opencode.json",
        evidence_dir=base,
        servers_key="mcp",
        scope_note=f"{note} (an opencode.jsonc beside it is refused: comments "
        "do not merge)",
        style="opencode",
    )


def _muse(context: Environment, level: str) -> ClientTarget:
    base = muse_config_dir(context)
    return ClientTarget(
        client="muse-code",
        label="Muse Code",
        level=level,
        config_path=(
            context.cwd / ".mcp.json" if level == "project" else base / "settings.json"
        ),
        evidence_dir=base,
        servers_key=_SERVERS_KEY,
        scope_note=(
            "project level: trusted Muse workspace"
            if level == "project"
            else "user level: every Muse Code session on this machine"
        ),
    )


_TARGETS: Final[dict[str, Callable[[Environment, str], ClientTarget]]] = {
    "claude-code": _claude_code,
    "claude-desktop": _claude_desktop,
    "cursor": _cursor,
    "codex": _codex,
    "opencode": _opencode,
    "muse-code": _muse,
}
CLIENT_CHOICES: Final = tuple(_TARGETS)


def resolve_target(
    client: str, context: Environment, level: str = "user"
) -> ClientTarget:
    """The registration surface for one `--client` token at a level; a
    one-file client answers with its user row whatever was asked (the
    installer refuses the mismatch, a reader just reads)."""
    return _TARGETS[client](context, level)


def _argv_of(entry: Any) -> list[str] | None:
    """The registered server's argv: `{command, args}` or OpenCode's
    `{command: [...]}`."""
    if not isinstance(entry, dict):
        return None
    command = entry.get("command")
    if isinstance(command, list):
        return [str(part) for part in command]
    if isinstance(command, str):
        args = entry.get("args", [])
        return [command, *(str(a) for a in args)] if isinstance(args, list) else None
    return None


def registered_argv(target: ClientTarget) -> list[str] | None:
    """Our server's argv as `target`'s file carries it; None when the file
    or the entry is absent (a reader: nothing is repaired)."""
    document = read_document(target.config_path)
    servers = document.get(target.servers_key) if document is not None else None
    return _argv_of(servers.get(SERVER_NAME)) if isinstance(servers, dict) else None
