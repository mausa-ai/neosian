"""`neosian mcp install` — self-setup (DESIGN §14.5, §22.6).

Prints the exact client registration by default; applies it only with
--write, merging key-preserving into the client's config file and
refusing — never creating — a missing config home (the shared rules live
in `shared/client_config.py`, beside the hook installer's; the clients'
rows in `targets.py`). Once per machine by default: the user-level entry
names the store and no mount, so the server derives each session's layout
from the directory the client spawns it in; `--level project` writes this
directory's file with its layout in the line. A file the client's own CLI
writes is print-only, with that command as the way to apply it. Pure over
an injected `Environment`. Never imports the MCP SDK — install never
serves.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, TextIO

from neosian._foundation.mcp.server import DEFAULT_ACTOR
from neosian._foundation.mcp.targets import (
    CLIENT_CHOICES,
    SERVER_NAME,
    ClientTarget,
    resolve_target,
)
from neosian._foundation.memory.settings import (
    CLIENT_TOKEN_ENV,
    DEFAULT_SCHEMA,
    POSTGRES_DSN_ENV,
    StoreSettings,
    StreamParser,
    add_store_arguments,
    check_login,
    format_mount,
    resolve_store_settings,
)
from neosian._foundation.shared.client_config import (
    FIX_BY_HAND,
    Environment,
    InstallError,
    add_level_argument,
    ensure_evidence,
    load_document,
    write_document,
)
from neosian._foundation.shared.exceptions import MemoryStoreError

__all__ = ["Environment"]  # re-exported: the entry point and the suite name it here

_SERVER_ARGV: Final = ("-m", "neosian.mcp")  # the one place the module path lives
_DEFAULT_ACTOR: Final = DEFAULT_ACTOR
_DESCRIPTION: Final = "Print or apply an MCP client registration for neosian memory."
_EPILOG: Final = (
    "Print mode (the default) puts the paste-able fragment on stdout and "
    "guidance on stderr; --write merges it into the client's config, "
    "preserving every other key. A missing client config directory is "
    "refused, never created. Postgres: set NEOSIAN_POSTGRES_DSN in the "
    "client's own environment — a DSN is never written into a registration."
)


@dataclass(frozen=True, slots=True)
class RegistrationEntry:
    """The server entry a client config carries."""

    command: str
    args: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"command": self.command, "args": list(self.args)}

    def render(self, style: str) -> dict[str, Any]:
        """The entry in a client's own shape."""
        if style == "opencode":
            return {
                "type": "local",
                "command": [self.command, *self.args],
                "enabled": True,
            }
        return self.to_json()

    def to_toml(self, *, servers_key: str, name: str) -> str:
        """The `[<servers_key>.<name>]` table; JSON strings are valid TOML
        basic strings, so one quoting rule serves both."""
        args = ", ".join(json.dumps(arg) for arg in self.args)
        return (
            f"[{servers_key}.{name}]\n"
            f"command = {json.dumps(self.command)}\n"
            f"args = [{args}]\n"
        )

    def apply_argv(self, name: str, cli: str) -> list[str]:
        """The client's own writer for the file it owns: Claude Code takes
        the entry as JSON at its user scope, Codex the argv after `--`."""
        if cli == "claude":
            entry = json.dumps(self.to_json())
            return [cli, "mcp", "add-json", "--scope", "user", name, entry]
        return [cli, "mcp", "add", name, "--", self.command, *self.args]

    def apply_line(self, name: str, cli: str) -> str:
        return shlex.join(self.apply_argv(name, cli))


def build_entry(settings: StoreSettings, *, executable: str) -> RegistrationEntry:
    """The resolved settings re-rendered as the server invocation.

    Absolute root (a client spawns the server from an arbitrary cwd),
    mounts in canonical `--mount` form (`format_mount`, ledger #75), the
    daemon URL when the store is the state process (its token rides the
    client's environment, `NEOSIAN_CLIENT_TOKEN`), and never the DSN —
    Postgres reaches the server through the client's own environment
    (`NEOSIAN_POSTGRES_DSN`), where a config file would be even more
    readable than argv.
    """
    args: list[str] = list(_SERVER_ARGV)
    if settings.root is not None:
        args += ["--root", str(settings.root.expanduser().resolve())]
    if settings.url is not None:
        args += ["--url", settings.url]
    for mount in settings.mounts:
        args += ["--mount", format_mount(mount)]
    if settings.actor != _DEFAULT_ACTOR:
        args += ["--actor", settings.actor]
    if settings.dsn is not None and settings.schema != DEFAULT_SCHEMA:
        args += ["--schema", settings.schema]
    return RegistrationEntry(command=executable, args=tuple(args))


def merge_entry(
    document: dict[str, Any],
    *,
    servers_key: str,
    name: str,
    entry: RegistrationEntry,
    path: Path,
    style: str = "mcpServers",
) -> dict[str, Any]:
    """A new document with our server registered; every other key preserved."""
    merged = dict(document)
    existing = merged.get(servers_key, {})
    if not isinstance(existing, dict):
        raise InstallError(
            f"{servers_key} in {path} is not a JSON object; " "refusing to rewrite it",
            FIX_BY_HAND,
        )
    servers: dict[str, Any] = dict(existing)
    servers[name] = entry.render(style)
    merged[servers_key] = servers
    return merged


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
    apply = entry.apply_line(SERVER_NAME, target.cli) if target.cli else None
    if json_output:
        payload = {
            "success": True,
            "client": target.client,
            "label": target.label,
            "level": target.level,
            "config_path": str(target.config_path),
            "servers_key": target.servers_key,
            "server_name": SERVER_NAME,
            "entry": entry.render(target.style),
            "written": written,
            "created": created,
            "apply": apply,
        }
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    fragment = {target.servers_key: {SERVER_NAME: entry.render(target.style)}}
    if apply is not None:
        # stdout is only the paste-able fragment: the table for a TOML file.
        if target.config_path.suffix == ".toml":
            out.write(entry.to_toml(servers_key=target.servers_key, name=SERVER_NAME))
        else:
            out.write(json.dumps(fragment, indent=2) + "\n")
        err.write(f"{target.label}: {target.scope_note}\n")
        err.write(f"target: {target.config_path}\n")
        err.write(f"hint: apply it with: {apply}\n")
    elif written:
        out.write(f"{'created' if created else 'updated'} {target.config_path}\n")
        if settings.root is not None:
            err.write(
                "hint: one writer per FileStore root (DESIGN §8) — nothing else "
                f"writes to {settings.root} while this server runs; for more "
                "than one writer run `neosian serve` and register --url\n"
            )
    else:
        # stdout is only the paste-able fragment: `> snippet.json` stays valid.
        out.write(json.dumps(fragment, indent=2) + "\n")
        err.write(f"{target.label}: {target.scope_note}\n")
        err.write(f"target: {target.config_path}\n")
        err.write(f"hint: re-run with --write to apply this to {target.config_path}\n")
    if settings.dsn is not None:
        err.write(
            f"hint: set {POSTGRES_DSN_ENV} in the client's own environment — "
            "it is deliberately never written into a registration\n"
        )
    if settings.url is not None:
        err.write(
            f"hint: set {CLIENT_TOKEN_ENV} in the client's own environment — "
            "the token is deliberately never written into a registration\n"
        )
    return 0


def _render_failure(
    exc: InstallError,
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
    add_level_argument(parser)
    add_store_arguments(parser, default_actor=_DEFAULT_ACTOR)
    try:
        args = parser.parse_args(list(argv))
        target = resolve_target(args.client, context, args.level)
        if target.level != args.level:
            parser.error(
                f"--level {args.level}: {target.label} keeps one file for every "
                f"project ({target.config_path}); it registers at the user level"
            )
        # Per machine the line names no mount: the server derives each
        # session's layout where the client spawns it (§22.6).
        layout = context.cwd if args.level == "project" else None
        settings = resolve_store_settings(parser, args, env, layout=layout)
        if not settings.mounts:
            check_login(parser)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2

    if settings.actor == _DEFAULT_ACTOR:
        # The installer knows the client; the stdio default does not.
        settings = replace(settings, actor=f"mcp:{args.client}")
    entry = build_entry(settings, executable=context.executable)
    created = False
    try:
        ensure_evidence(target.label, target.evidence_dir)
        if args.write and target.cli is not None:
            raise InstallError(
                f"{target.label}'s own CLI writes {target.config_path}; --write "
                "is not offered",
                f"apply it with: {entry.apply_line(SERVER_NAME, target.cli)}",
            )
        commented = target.config_path.with_suffix(".jsonc")
        if args.write and target.style == "opencode" and commented.is_file():
            raise InstallError(
                f"{commented} carries comments a merge would lose; refusing to "
                "write a second config beside it",
                "re-run without --write and paste the entry into it yourself",
            )
        if args.write:
            document = load_document(target.config_path)
            created = not target.config_path.exists()
            merged = merge_entry(
                document,
                servers_key=target.servers_key,
                name=SERVER_NAME,
                entry=entry,
                path=target.config_path,
                style=target.style,
            )
            write_document(target.config_path, merged)
    except InstallError as exc:
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
