"""`neosian record install` — the hook installer (DESIGN §20.9, §22.6).

The `mcp install` twin for a foreign agent's hooks: prints the exact
hooks fragment by default, applies it only with --write, merging
key-preserving into the client's settings — every other hook survives,
ours is replaced, so a re-run is idempotent. Once per machine by default:
the user-level line names the store and no mount, and the verb derives
each session's layout from the client's project directory (`--project`,
where the client has a stable one) or its working directory; `--level
project` writes this directory's file with its layout in the line. Hooks
are a second writer beside the agent's MCP server, so the full record
rides the state process (`--url`) or Postgres (§8's one-writer rule). A
client row (`targets.py`) exists only while its walkthrough is green.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import replace
from importlib import resources
from typing import TYPE_CHECKING, Any, Final, TextIO

from neosian._foundation.memory.settings import (
    CLIENT_TOKEN_ENV,
    DEFAULT_SCHEMA,
    POSTGRES_DSN_ENV,
    StreamParser,
    check_login,
    format_mount,
)
from neosian._foundation.record.settings import (
    DEFAULT_AGENT,
    RecordSettings,
    add_record_arguments,
    resolve_record_settings,
)
from neosian._foundation.record.targets import (
    CLIENT_CHOICES,
    RECORD_ARGV,
    HookTarget,
    installed_argv,
    is_ours,
    resolve_target,
)
from neosian._foundation.shared.client_config import (
    FIX_BY_HAND,
    Environment,
    InstallError,
    add_level_argument,
    ensure_evidence,
    load_document,
    write_document,
    write_text,
)
from neosian._foundation.shared.exceptions import MemoryStoreError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

HOOK_EVENTS: Final = ("UserPromptSubmit", "PostToolUse", "Stop", "SessionStart")
HOOKS_KEY: Final = "hooks"
_PLUGIN_ASSET: Final = "clients/opencode-record.js"
_PLUGIN_ARGV_SLOT: Final = "__NEOSIAN_RECORD_ARGV__"
_DESCRIPTION: Final = "Print or apply a foreign agent's hooks for neosian record."
_EPILOG: Final = (
    "Print mode (the default) puts the paste-able hooks fragment on stdout "
    "and guidance on stderr; --write merges it into the client's settings, "
    "preserving every other hook and key (ours is replaced, so a re-run is "
    "idempotent). A missing client config directory is refused, never "
    "created. Postgres: set NEOSIAN_POSTGRES_DSN in the client's own "
    "environment — a DSN is never written into a hook line."
)


def build_argv(settings: RecordSettings, *, executable: str) -> list[str]:
    """The resolved settings re-rendered as the verb's argv — the same
    layout `mcp install` renders (absolute root, the mounts that were
    named in canonical form, the URL verbatim, never the DSN), plus the
    agent's kind and an absolute spool (hooks run wherever the session
    does, worktrees included)."""
    store = settings.store
    args: list[str] = [executable, *RECORD_ARGV]
    if store.root is not None:
        args += ["--root", str(store.root.expanduser().resolve())]
    if store.url is not None:
        args += ["--url", store.url]
    for mount in store.mounts:
        args += ["--mount", format_mount(mount)]
    if store.dsn is not None and store.schema != DEFAULT_SCHEMA:
        args += ["--schema", store.schema]
    if settings.agent != DEFAULT_AGENT:
        args += ["--agent", settings.agent]
    args += ["--spool", str(settings.spool.expanduser().resolve())]
    return args


def build_command(
    settings: RecordSettings, *, executable: str, project_token: str | None = None
) -> str:
    """`build_argv` as one shell line — what a hooks file carries. A line
    that names no mount ends on the client's project expression, raw:
    quoting it (as `shlex.join` would) stops the shell expanding it."""
    line = shlex.join(build_argv(settings, executable=executable))
    if project_token is not None and not settings.store.mounts:
        return f"{line} {project_token}"
    return line


def render_plugin(argv: Sequence[str]) -> str:
    """The OpenCode plugin with the verb's argv in its one slot."""
    template = (
        resources.files("neosian.assets").joinpath(_PLUGIN_ASSET).read_text("utf-8")
    )
    return template.replace(_PLUGIN_ARGV_SLOT, json.dumps(list(argv)))


def hook_fragment(command: str) -> dict[str, Any]:
    """The `hooks` fragment: the one command on the four events — three
    that write the span and `SessionStart`, whose stdout is the context
    (no matcher: the verb reads `source` itself)."""
    return {
        HOOKS_KEY: {
            event: [{"hooks": [{"type": "command", "command": command}]}]
            for event in HOOK_EVENTS
        }
    }


def merge_hooks(
    document: dict[str, Any], fragment: dict[str, Any], *, path: Path
) -> dict[str, Any]:
    """A new document with our hooks in place; every other key, event and
    group preserved — a group carrying our command is replaced whole."""
    merged = dict(document)
    existing = merged.get(HOOKS_KEY, {})
    if not isinstance(existing, dict):
        raise InstallError(
            f"{HOOKS_KEY} in {path} is not a JSON object; refusing to rewrite it",
            FIX_BY_HAND,
        )
    hooks: dict[str, Any] = dict(existing)
    for event, groups in fragment[HOOKS_KEY].items():
        current = hooks.get(event, [])
        if not isinstance(current, list):
            raise InstallError(
                f"{HOOKS_KEY}.{event} in {path} is not a JSON array; "
                "refusing to rewrite it",
                FIX_BY_HAND,
            )
        hooks[event] = [g for g in current if not is_ours(g)] + list(groups)
    merged[HOOKS_KEY] = hooks
    return merged


def strip_hooks(document: dict[str, Any]) -> dict[str, Any]:
    """`document` without our groups; an event left with none goes too."""
    hooks = document.get(HOOKS_KEY)
    if not isinstance(hooks, dict):
        return document
    kept = {
        event: (
            [g for g in groups if not is_ours(g)]
            if isinstance(groups, list)
            else groups
        )
        for event, groups in hooks.items()
    }
    return {**document, HOOKS_KEY: {e: g for e, g in kept.items() if g != []}}


def displace(target: HookTarget, *, write: bool) -> str | None:
    """One level per client (§22.6): every client merges its hook sources,
    so ours at two levels fires twice and lands each span twice. Remove ours
    from `target` (the plugin file is ours whole); the path it sat in, None
    when it was not there. `write=False` only names it."""
    if installed_argv(target) is None:
        return None
    if write and target.plugin:
        target.config_path.unlink()
    elif write:
        document = load_document(target.config_path)
        write_document(target.config_path, strip_hooks(document))
    return str(target.config_path)


def _render_success(
    *,
    target: HookTarget,
    argv: Sequence[str],
    command: str,
    settings: RecordSettings,
    displaced: str | None,
    written: bool,
    created: bool,
    json_output: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    fragment = hook_fragment(command)
    plugin = render_plugin(argv) if target.plugin else None
    if json_output:
        payload = {
            "success": True,
            "client": target.client,
            "label": target.label,
            "level": target.level,
            "config_path": str(target.config_path),
            "hooks": None if target.plugin else fragment[HOOKS_KEY],
            "plugin": plugin,
            "command": command,
            "displaced": displaced,
            "written": written,
            "created": created,
        }
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    if written:
        out.write(f"{'created' if created else 'updated'} {target.config_path}\n")
        if displaced is not None:
            out.write(f"removed ours from {displaced}\n")
    else:
        # stdout is only the paste-able artifact: the fragment, or the
        # plugin source — `> file` stays valid either way.
        out.write(
            plugin if plugin is not None else json.dumps(fragment, indent=2) + "\n"
        )
        err.write(f"{target.label}: {target.scope_note}\n")
        err.write(f"target: {target.config_path}\n")
        err.write(f"hint: re-run with --write to apply this to {target.config_path}\n")
        if displaced is not None:
            err.write(
                f"hint: --write also removes ours from {displaced}: one level "
                "per client, or every span lands twice\n"
            )
    if target.trust_hint is not None:
        err.write(f"hint: {target.trust_hint}\n")
    if settings.store.root is not None:
        err.write(
            "hint: one writer per FileStore root (DESIGN §8) — the home is one "
            "root shared by every project's hooks and MCP servers; for more "
            "than one writer run `neosian serve` and install with --url, or "
            "use Postgres\n"
        )
    if settings.store.dsn is not None:
        err.write(
            f"hint: set {POSTGRES_DSN_ENV} in the client's own environment — "
            "it is deliberately never written into a hook line\n"
        )
    if settings.store.url is not None:
        err.write(
            f"hint: set {CLIENT_TOKEN_ENV} in the client's own environment — "
            "the token is deliberately never written into a hook line\n"
        )
    return 0


def _render_failure(
    exc: InstallError,
    *,
    target: HookTarget,
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
    prog: str = "neosian record install",
) -> int:
    """Parse and execute one install; construct nothing on exit 2."""
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    parser.add_argument(
        "--client",
        required=True,
        choices=CLIENT_CHOICES,
        help="the agent whose hooks call neosian record",
    )
    parser.add_argument(
        "--write", action="store_true", help="apply the hooks (default: print them)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="print one JSON envelope on stdout",
    )
    add_level_argument(parser)
    add_record_arguments(parser)
    try:
        args = parser.parse_args(list(argv))
        # Per machine the line names no mount: the verb derives each
        # session's layout from the client's project directory (§22.6).
        layout = context.cwd if args.level == "project" else None
        settings = resolve_record_settings(parser, args, env, layout=layout)
        if not settings.store.mounts:
            check_login(parser)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2

    target = resolve_target(args.client, context, args.level)
    if settings.agent == DEFAULT_AGENT:
        # The installer knows the client; the verb's default does not.
        settings = replace(settings, agent=args.client)
    argv = build_argv(settings, executable=context.executable)
    command = build_command(
        settings, executable=context.executable, project_token=target.project_token
    )
    if args.client == "muse-code":
        from neosian._foundation.record.muse import run_install as muse_install

        return muse_install(
            context,
            settings,
            level=args.level,
            command=command,
            write=args.write,
            json_output=args.json_output,
            out=out,
            err=err,
        )
    if args.client == "cursor":
        from neosian._foundation.record.cursor_install import (
            run_install as cursor_install,
        )

        return cursor_install(
            target,
            context,
            command=command,
            write=args.write,
            json_output=args.json_output,
            out=out,
            err=err,
        )
    created = False
    displaced: str | None = None
    other = resolve_target(
        args.client, context, "project" if args.level == "user" else "user"
    )
    try:
        ensure_evidence(target.label, target.evidence_dir)
        if other.config_path == target.config_path:
            pass  # run from the client's own home: the two levels are one file
        elif args.level == "user":
            displaced = displace(other, write=args.write)
        elif installed_argv(other) is not None:
            # Refused in print mode too: it must not promise a refused write.
            raise InstallError(
                f"{target.label} already carries the neosian hooks at the user "
                f"level ({other.config_path}); both would fire",
                "keep them and name this project's scope with NEOSIAN_SCOPE in "
                f"the client's environment, or remove ours from {other.config_path}",
            )
        if args.write:
            created = not target.config_path.exists()
            # The project's own config directory, or `plugins/` inside the
            # client's: never the client's home itself, refused above.
            target.config_path.parent.mkdir(parents=True, exist_ok=True)
            if target.plugin:
                write_text(target.config_path, render_plugin(argv))
            else:
                document = load_document(target.config_path)
                merged = merge_hooks(
                    document, hook_fragment(command), path=target.config_path
                )
                write_document(target.config_path, merged)
    except InstallError as exc:
        return _render_failure(
            exc, target=target, json_output=args.json_output, out=out, err=err
        )
    return _render_success(
        target=target,
        argv=argv,
        command=command,
        settings=settings,
        displaced=displaced,
        written=args.write,
        created=created,
        json_output=args.json_output,
        out=out,
        err=err,
    )
