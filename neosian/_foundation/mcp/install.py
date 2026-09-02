"""`neosian mcp install` — self-setup (DESIGN §14.5).

Prints the exact client registration by default; applies it only with
--write, merging key-preserving into the client's config file and
refusing — never creating — a missing config home. Pure over an
injected `Environment`: path resolution reads no ambient state, which
keeps the suite monkeypatch-free. Never imports the MCP SDK — install
works without the extra (it never serves).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TextIO

from neosian._foundation.memory.settings import (
    DEFAULT_SCHEMA,
    POSTGRES_DSN_ENV,
    StoreSettings,
    StreamParser,
    add_store_arguments,
    format_mount,
    resolve_store_settings,
)
from neosian._foundation.shared.exceptions import MemoryStoreError
from neosian._foundation.shared.fileio import PRIVATE_FILE, atomic_write

SERVER_NAME: Final = "neosian-memory"
_SERVER_ARGV: Final = ("-m", "neosian.mcp")  # the one place the module path lives
_SERVERS_KEY: Final = "mcpServers"
_DEFAULT_ACTOR: Final = "mcp"
_DESCRIPTION: Final = "Print or apply an MCP client registration for neosian memory."
_EPILOG: Final = (
    "Print mode (the default) puts the paste-able JSON fragment on stdout "
    "and guidance on stderr; --write merges it into the client's config, "
    "preserving every other key. A missing client config directory is "
    "refused, never created. Postgres: set NEOSIAN_POSTGRES_DSN in the "
    "client's own environment — a DSN is never written into a registration."
)
_FIX_BY_HAND: Final = (
    "fix it by hand, or re-run without --write and paste the entry yourself"
)


@dataclass(frozen=True, slots=True)
class Environment:
    """Everything path resolution reads — injected, never ambient."""

    home: Path
    cwd: Path
    platform: str
    env: Mapping[str, str]
    executable: str


@dataclass(frozen=True, slots=True)
class ClientTarget:
    """One client's registration surface — a row, so reversals are cheap."""

    client: str
    label: str
    config_path: Path
    evidence_dir: Path  # must already exist; NEVER created
    servers_key: str
    scope_note: str


@dataclass(frozen=True, slots=True)
class RegistrationEntry:
    """The server entry a client config carries."""

    command: str
    args: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"command": self.command, "args": list(self.args)}


class _InstallError(Exception):
    """A tier-1 refusal: environment or config-file trouble, with its fix."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def _claude_code(context: Environment) -> ClientTarget:
    return ClientTarget(
        client="claude-code",
        label="Claude Code",
        config_path=context.cwd / ".mcp.json",
        evidence_dir=context.home / ".claude",
        servers_key=_SERVERS_KEY,
        scope_note="project scope — .mcp.json travels with this directory's repo",
    )


def _claude_desktop(context: Environment) -> ClientTarget:
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
        config_path=base / "claude_desktop_config.json",
        evidence_dir=base,
        servers_key=_SERVERS_KEY,
        scope_note="user scope — applies to every Claude Desktop conversation",
    )


def _cursor(context: Environment) -> ClientTarget:
    base = context.home / ".cursor"
    return ClientTarget(
        client="cursor",
        label="Cursor",
        config_path=base / "mcp.json",
        evidence_dir=base,
        servers_key=_SERVERS_KEY,
        scope_note="user scope — applies to every Cursor project",
    )


_TARGETS: Final[dict[str, Callable[[Environment], ClientTarget]]] = {
    "claude-code": _claude_code,
    "claude-desktop": _claude_desktop,
    "cursor": _cursor,
}
CLIENT_CHOICES: Final = tuple(_TARGETS)


def resolve_target(client: str, context: Environment) -> ClientTarget:
    """The registration surface for one `--client` token."""
    return _TARGETS[client](context)


def build_entry(settings: StoreSettings, *, executable: str) -> RegistrationEntry:
    """The resolved settings re-rendered as the server invocation.

    Absolute root (a client spawns the server from an arbitrary cwd),
    mounts in canonical `--mount` form (`format_mount`, ledger #75), and
    never the DSN — Postgres reaches the server through the client's own
    environment (`NEOSIAN_POSTGRES_DSN`), where a config file would be
    even more readable than argv.
    """
    args: list[str] = list(_SERVER_ARGV)
    if settings.root is not None:
        args += ["--root", str(settings.root.expanduser().resolve())]
    for mount in settings.mounts:
        args += ["--mount", format_mount(mount)]
    if settings.actor != _DEFAULT_ACTOR:
        args += ["--actor", settings.actor]
    if settings.dsn is not None and settings.schema != DEFAULT_SCHEMA:
        args += ["--schema", settings.schema]
    return RegistrationEntry(command=executable, args=tuple(args))


def load_document(path: Path) -> dict[str, Any]:
    """The existing config as a dict, or {} when the file does not exist."""
    if not path.exists():
        return {}
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _InstallError(f"cannot read {path}: {exc}", _FIX_BY_HAND) from exc
    except json.JSONDecodeError as exc:
        raise _InstallError(
            f"{path} is not valid JSON; refusing to rewrite it", _FIX_BY_HAND
        ) from exc
    if not isinstance(raw, dict):
        raise _InstallError(
            f"{path} is not a JSON object; refusing to rewrite it", _FIX_BY_HAND
        )
    document: dict[str, Any] = raw
    return document


def merge_entry(
    document: dict[str, Any],
    *,
    servers_key: str,
    name: str,
    entry: RegistrationEntry,
    path: Path,
) -> dict[str, Any]:
    """A new document with our server registered; every other key preserved."""
    merged = dict(document)
    existing = merged.get(servers_key, {})
    if not isinstance(existing, dict):
        raise _InstallError(
            f"{servers_key} in {path} is not a JSON object; " "refusing to rewrite it",
            _FIX_BY_HAND,
        )
    servers: dict[str, Any] = dict(existing)
    servers[name] = entry.to_json()
    merged[servers_key] = servers
    return merged


def _ensure_evidence(target: ClientTarget) -> None:
    if not target.evidence_dir.is_dir():
        raise _InstallError(
            f"{target.label} is not installed here: "
            f"{target.evidence_dir} does not exist",
            "install the client first — neosian never creates another "
            "program's config directory",
        )


def _write_document(path: Path, document: dict[str, Any]) -> None:
    # MCP configs carry other servers' credentials: an existing file keeps
    # its mode, a new one is born private — never the umask default.
    text = json.dumps(document, indent=2) + "\n"
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else PRIVATE_FILE
        atomic_write(path, text, mode=mode)
    except OSError as exc:
        raise _InstallError(f"cannot write {path}: {exc}", _FIX_BY_HAND) from exc


def _render_success(
    *,
    target: ClientTarget,
    entry: RegistrationEntry,
    settings: StoreSettings,
    written: bool,
    created: bool,
    json_output: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    if json_output:
        payload = {
            "success": True,
            "client": target.client,
            "label": target.label,
            "config_path": str(target.config_path),
            "servers_key": target.servers_key,
            "server_name": SERVER_NAME,
            "entry": entry.to_json(),
            "written": written,
            "created": created,
        }
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    if written:
        out.write(f"{'created' if created else 'updated'} {target.config_path}\n")
        if settings.root is not None:
            err.write(
                "hint: one writer per FileStore root (DESIGN §8) — do not "
                f"write to {settings.root} while this server is running\n"
            )
    else:
        # stdout is only the paste-able fragment: `> snippet.json` stays valid.
        fragment = {target.servers_key: {SERVER_NAME: entry.to_json()}}
        out.write(json.dumps(fragment, indent=2) + "\n")
        err.write(f"{target.label}: {target.scope_note}\n")
        err.write(f"target: {target.config_path}\n")
        err.write(f"hint: re-run with --write to apply this to {target.config_path}\n")
    if settings.dsn is not None:
        err.write(
            f"hint: set {POSTGRES_DSN_ENV} in the client's own environment — "
            "it is deliberately never written into a registration\n"
        )
    return 0


def _render_failure(
    exc: _InstallError,
    *,
    target: ClientTarget,
    json_output: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    if json_output:
        payload = {
            "success": False,
            "error": exc.message,
            "hint": exc.hint,
            "client": target.client,
            "config_path": str(target.config_path),
        }
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        err.write(f"error: {exc.message}\n")
        err.write(f"hint: {exc.hint}\n")
    return 1


def run_install(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    context: Environment,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian mcp install",
) -> int:
    """Parse and execute one install; construct nothing on exit 2."""
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    parser.add_argument(
        "--client",
        required=True,
        choices=CLIENT_CHOICES,
        help="the MCP client to register with",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="apply the registration (default: print it)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="print one JSON envelope on stdout",
    )
    add_store_arguments(parser, default_actor=_DEFAULT_ACTOR)
    try:
        args = parser.parse_args(list(argv))
        settings = resolve_store_settings(parser, args, env)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2

    target = resolve_target(args.client, context)
    entry = build_entry(settings, executable=context.executable)
    created = False
    try:
        _ensure_evidence(target)
        if args.write:
            document = load_document(target.config_path)
            created = not target.config_path.exists()
            merged = merge_entry(
                document,
                servers_key=target.servers_key,
                name=SERVER_NAME,
                entry=entry,
                path=target.config_path,
            )
            _write_document(target.config_path, merged)
    except _InstallError as exc:
        return _render_failure(
            exc, target=target, json_output=args.json_output, out=out, err=err
        )
    return _render_success(
        target=target,
        entry=entry,
        settings=settings,
        written=args.write,
        created=created,
        json_output=args.json_output,
        out=out,
        err=err,
    )
