"""`neosian record install` — the hook installer (DESIGN §20.9).

The `mcp install` twin for a foreign agent's hooks: prints the exact
hooks fragment by default, applies it only with --write, merging
key-preserving into the client's settings — every other hook survives,
ours is replaced, so a re-run is idempotent. It renders the same mount
layout from the same flags as `mcp install`; hooks are a second writer
beside the agent's MCP server, so the full record rides the state
process (`--url`) or Postgres (§8's one-writer rule). Claude Code first,
walkthrough-gated: a client row exists only while its walkthrough is
green.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TextIO

from neosian._foundation.memory.settings import (
    CLIENT_TOKEN_ENV,
    DEFAULT_SCHEMA,
    POSTGRES_DSN_ENV,
    StreamParser,
    format_mount,
)
from neosian._foundation.record.settings import (
    DEFAULT_AGENT,
    RecordSettings,
    add_record_arguments,
    resolve_record_settings,
)
from neosian._foundation.shared.client_config import (
    FIX_BY_HAND,
    Environment,
    InstallError,
    ensure_evidence,
    load_document,
    write_document,
    write_text,
)
from neosian._foundation.shared.exceptions import MemoryStoreError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

HOOK_EVENTS: Final = ("UserPromptSubmit", "PostToolUse", "Stop", "SessionStart")
HOOKS_KEY: Final = "hooks"
_RECORD_ARGV: Final = ("-m", "neosian.record")  # the one place the module path lives
_MARKER: Final = " ".join(_RECORD_ARGV)  # how ours is recognised in a merge
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


@dataclass(frozen=True, slots=True)
class HookTarget:
    """One client's hooks surface — a row, so reversals are cheap."""

    client: str
    label: str
    config_path: Path
    evidence_dir: Path  # must already exist; NEVER created
    scope_note: str
    trust_hint: str | None = None  # what the client needs before it loads the file
    # A plugin client (OpenCode) has no shell hooks: the file is ours whole
    # — a rendered plugin, written and overwritten, never merged.
    plugin: bool = False


def _claude_code(context: Environment) -> HookTarget:
    return HookTarget(
        client="claude-code",
        label="Claude Code",
        config_path=context.cwd / ".claude" / "settings.json",
        evidence_dir=context.home / ".claude",
        scope_note="project scope — .claude/settings.json travels with this "
        "directory's repo",
    )


def codex_home(context: Environment) -> Path:
    """`$CODEX_HOME` moves every Codex file; the default is `~/.codex`."""
    override = context.env.get("CODEX_HOME")
    return Path(override) if override else context.home / ".codex"


def _codex(context: Environment) -> HookTarget:
    return HookTarget(
        client="codex",
        label="Codex",
        config_path=context.cwd / ".codex" / "hooks.json",
        evidence_dir=codex_home(context),
        scope_note="project scope — .codex/hooks.json travels with this "
        "directory's repo",
        trust_hint="Codex loads project hooks only for a trusted project: "
        f'[projects."{context.cwd}"] trust_level = "trusted" in its config.toml, '
        "or accept the trust prompt on first run",
    )


def opencode_config_dir(context: Environment) -> Path:
    """`$OPENCODE_CONFIG_DIR` moves OpenCode's config; the default is
    `~/.config/opencode`."""
    override = context.env.get("OPENCODE_CONFIG_DIR")
    return Path(override) if override else context.home / ".config" / "opencode"


def _opencode(context: Environment) -> HookTarget:
    return HookTarget(
        client="opencode",
        label="OpenCode",
        config_path=context.cwd / ".opencode" / "plugins" / "neosian-record.js",
        evidence_dir=opencode_config_dir(context),
        scope_note="project scope — .opencode/plugins/neosian-record.js travels "
        "with this directory's repo",
        plugin=True,
    )


_TARGETS: Final[dict[str, Callable[[Environment], HookTarget]]] = {
    "claude-code": _claude_code,
    "codex": _codex,
    "opencode": _opencode,
}
CLIENT_CHOICES: Final = tuple(_TARGETS)


def resolve_target(client: str, context: Environment) -> HookTarget:
    """The hooks surface for one `--client` token."""
    return _TARGETS[client](context)


def build_argv(settings: RecordSettings, *, executable: str) -> list[str]:
    """The resolved settings re-rendered as the verb's argv — the same
    layout `mcp install` renders (absolute root, canonical mounts, the
    URL verbatim, never the DSN), plus the agent's kind and an absolute
    spool (hooks run in the project's cwd, worktrees included)."""
    store = settings.store
    args: list[str] = [executable, *_RECORD_ARGV]
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


def build_command(settings: RecordSettings, *, executable: str) -> str:
    """`build_argv` as one shell line — what a hooks file carries."""
    return shlex.join(build_argv(settings, executable=executable))


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


def _is_ours(group: object) -> bool:
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    return any(
        isinstance(hook, dict) and _MARKER in str(hook.get("command", ""))
        for hook in group["hooks"]
    )


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
        hooks[event] = [g for g in current if not _is_ours(g)] + list(groups)
    merged[HOOKS_KEY] = hooks
    return merged


def _render_success(
    *,
    target: HookTarget,
    argv: Sequence[str],
    settings: RecordSettings,
    written: bool,
    created: bool,
    json_output: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    command = shlex.join(argv)
    fragment = hook_fragment(command)
    plugin = render_plugin(argv) if target.plugin else None
    if json_output:
        payload = {
            "success": True,
            "client": target.client,
            "label": target.label,
            "config_path": str(target.config_path),
            "hooks": None if target.plugin else fragment[HOOKS_KEY],
            "plugin": plugin,
            "command": command,
            "written": written,
            "created": created,
        }
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    if written:
        out.write(f"{'created' if created else 'updated'} {target.config_path}\n")
    else:
        # stdout is only the paste-able artifact: the fragment, or the
        # plugin source — `> file` stays valid either way.
        out.write(
            plugin if plugin is not None else json.dumps(fragment, indent=2) + "\n"
        )
        err.write(f"{target.label}: {target.scope_note}\n")
        err.write(f"target: {target.config_path}\n")
        err.write(f"hint: re-run with --write to apply this to {target.config_path}\n")
    if target.trust_hint is not None:
        err.write(f"hint: {target.trust_hint}\n")
    if settings.store.root is not None:
        err.write(
            "hint: one writer per FileStore root (DESIGN §8) — hooks beside an "
            "MCP server on the same root are two; route both through the "
            "state process (--url) or Postgres\n"
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
    add_record_arguments(parser)
    try:
        args = parser.parse_args(list(argv))
        settings = resolve_record_settings(parser, args, env)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2

    target = resolve_target(args.client, context)
    if settings.agent == DEFAULT_AGENT:
        # The installer knows the client; the verb's default does not.
        settings = replace(settings, agent=args.client)
    argv = build_argv(settings, executable=context.executable)
    created = False
    try:
        ensure_evidence(target.label, target.evidence_dir)
        if args.write:
            created = not target.config_path.exists()
            # The project's own config directory, like `.mcp.json`'s parent —
            # not the client's home, which is refused above.
            target.config_path.parent.mkdir(parents=True, exist_ok=True)
            if target.plugin:
                write_text(target.config_path, render_plugin(argv))
            else:
                document = load_document(target.config_path)
                fragment = hook_fragment(shlex.join(argv))
                merged = merge_hooks(document, fragment, path=target.config_path)
                write_document(target.config_path, merged)
    except InstallError as exc:
        return _render_failure(
            exc, target=target, json_output=args.json_output, out=out, err=err
        )
    return _render_success(
        target=target,
        argv=argv,
        settings=settings,
        written=args.write,
        created=created,
        json_output=args.json_output,
        out=out,
        err=err,
    )
