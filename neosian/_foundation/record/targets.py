"""The hook clients' surfaces: one row per client and level (DESIGN §20.9,
§22.6).

A row names the file a client loads its hooks from, the directory that
proves the client is installed, and what the line needs so that one
registration serves every project: the shell expression for the client's
stable project directory (`project_token`), where its working directory
is not one. `installed_argv` is how an installed registration is
recognised again, by the installer and by `neosian status` alike.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace
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

RECORD_ARGV: Final = ("-m", "neosian.record")  # the one place the module path lives
MARKER: Final = " ".join(RECORD_ARGV)  # how ours is recognised in a file
_PLUGIN_ARGV: Final = re.compile(r'\[[^\[\]]*"-m", "neosian\.record"[^\[\]]*\]')
_PLUGIN_FILE: Final = "neosian-record.js"


@dataclass(frozen=True, slots=True)
class HookTarget:
    """One client's hooks surface — a row, so reversals are cheap."""

    client: str
    label: str
    level: str
    config_path: Path
    evidence_dir: Path  # must already exist; NEVER created
    scope_note: str
    trust_hint: str | None = None  # what the client needs before it loads the file
    # A plugin client (OpenCode) has no shell hooks: the file is ours whole
    # — a rendered plugin, written and overwritten, never merged.
    plugin: bool = False
    # Appended raw to a line that names no mount: the client's own
    # expression for the session's project directory. Claude Code runs a
    # hook in a working directory its `cd` moves; this variable stays put.
    project_token: str | None = None


def _claude_code(context: Environment, level: str) -> HookTarget:
    if level == "project":
        return HookTarget(
            client="claude-code",
            label="Claude Code",
            level=level,
            config_path=context.cwd / ".claude" / "settings.json",
            evidence_dir=claude_home(context),
            scope_note="project level: .claude/settings.json travels with this "
            "directory's repo",
        )
    return HookTarget(
        client="claude-code",
        label="Claude Code",
        level=level,
        config_path=claude_home(context) / "settings.json",
        evidence_dir=claude_home(context),
        scope_note="user level: every Claude Code session on this machine",
        project_token='--project "$CLAUDE_PROJECT_DIR"',
    )


def _codex(context: Environment, level: str) -> HookTarget:
    if level == "project":
        return HookTarget(
            client="codex",
            label="Codex",
            level=level,
            config_path=context.cwd / ".codex" / "hooks.json",
            evidence_dir=codex_home(context),
            scope_note="project level: .codex/hooks.json travels with this "
            "directory's repo",
            trust_hint="Codex loads project hooks only for a trusted project: "
            f'[projects."{context.cwd}"] trust_level = "trusted" in its '
            "config.toml, or accept the trust prompt on first run",
        )
    return HookTarget(
        client="codex",
        label="Codex",
        level=level,
        config_path=codex_home(context) / "hooks.json",
        evidence_dir=codex_home(context),
        scope_note="user level: every Codex session on this machine",
        trust_hint="Codex runs a new hook once you have reviewed it: open "
        "/hooks in Codex and approve it",
    )


def _opencode(context: Environment, level: str) -> HookTarget:
    base = opencode_config_dir(context)
    where = context.cwd / ".opencode" if level == "project" else base
    return HookTarget(
        client="opencode",
        label="OpenCode",
        level=level,
        config_path=where / "plugins" / _PLUGIN_FILE,
        evidence_dir=base,
        scope_note=(
            f"project level: .opencode/plugins/{_PLUGIN_FILE} travels with this "
            "directory's repo"
            if level == "project"
            else "user level: every OpenCode session on this machine"
        ),
        plugin=True,
    )


def _muse(context: Environment, level: str) -> HookTarget:
    base = muse_config_dir(context)
    return HookTarget(
        client="muse-code",
        label="Muse Code",
        level=level,
        config_path=(
            context.cwd / ".muse" / "hooks.json"
            if level == "project"
            else base / "settings.json"
        ),
        evidence_dir=base,
        scope_note=(
            "project level: trusted Muse workspace"
            if level == "project"
            else "user level: every Muse Code session on this machine"
        ),
    )


_TARGETS: Final[dict[str, Callable[[Environment, str], HookTarget]]] = {
    "claude-code": _claude_code,
    "codex": _codex,
    "opencode": _opencode,
    "muse-code": _muse,
}
CLIENT_CHOICES: Final = tuple(_TARGETS)


def resolve_target(
    client: str, context: Environment, level: str = "user"
) -> HookTarget:
    """The hooks surface for one `--client` token at a level."""
    return _TARGETS[client](context, level)


def is_ours(group: object) -> bool:
    """A hooks group that carries our command."""
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    return any(
        isinstance(hook, dict) and MARKER in str(hook.get("command", ""))
        for hook in group["hooks"]
    )


def _hook_argv(document: dict[str, Any]) -> list[str] | None:
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return None
    for groups in hooks.values():
        for group in groups if isinstance(groups, list) else []:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                command = hook.get("command", "") if isinstance(hook, dict) else ""
                if MARKER in str(command):
                    return shlex.split(str(command))
    return None


def _plugin_argv(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    match = _PLUGIN_ARGV.search(path.read_text(encoding="utf-8"))
    if match is None:
        return None
    try:
        parsed: Any = json.loads(match.group(0))
    except ValueError:
        return None
    return [str(p) for p in parsed] if isinstance(parsed, list) else None


def installed_argv(target: HookTarget) -> list[str] | None:
    """Our verb's argv as `target`'s file carries it; None when the file or
    our command is absent (a reader: nothing is repaired)."""
    if target.plugin:
        return _plugin_argv(target.config_path)
    document = read_document(target.config_path)
    return _hook_argv(document) if document is not None else None


def active_targets(
    client: str, context: Environment, level: str
) -> tuple[HookTarget, ...]:
    """Every active hook file at a level, including Muse's managed source."""
    target = resolve_target(client, context, level)
    if client != "muse-code" or level != "user":
        return (target,)
    from neosian._foundation.shared.muse_config import managed_path

    document = read_document(target.config_path) or {}
    managed = managed_path(document, target.config_path)
    if managed is None or managed == target.config_path:
        return (target,)
    return (replace(target, config_path=managed), target)
