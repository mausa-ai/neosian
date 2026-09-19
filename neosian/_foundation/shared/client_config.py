"""Editing another program's config file — the installers' shared half.

`neosian mcp install` (DESIGN §14.5) and `neosian record install` (§20.9)
print a registration by default and apply it only with --write, merging
key-preserving into a JSON document the client owns. Three rules, held
here once: a missing client home is refused, never created; an existing
file keeps its mode and a new one is born private; a file that is not a
JSON object is refused, never rewritten. Pure over an injected
`Environment`: path resolution reads no ambient state, which keeps the
suites monkeypatch-free.

A registration has a **level** (§22.6): `user`, the client's own config,
written once per machine with the layout derived per session, or
`project`, this directory's files with its layout written into the line.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.shared.fileio import PRIVATE_FILE, atomic_write

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping

FIX_BY_HAND: Final = (
    "fix it by hand, or re-run without --write and paste the entry yourself"
)
LEVELS: Final = ("user", "project")


@dataclass(frozen=True, slots=True)
class Environment:
    """Everything path resolution reads — injected, never ambient."""

    home: Path
    cwd: Path
    platform: str
    env: Mapping[str, str]
    executable: str


def add_level_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--level",
        choices=LEVELS,
        default="user",
        help="user: the client's own config, once per machine, the layout "
        "derived per session (default); project: this directory's files, its "
        "layout written into the line",
    )


def claude_home(context: Environment) -> Path:
    """`$CLAUDE_CONFIG_DIR` moves Claude Code's files; the default is
    `~/.claude`."""
    override = context.env.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else context.home / ".claude"


def codex_home(context: Environment) -> Path:
    """`$CODEX_HOME` moves every Codex file; the default is `~/.codex`."""
    override = context.env.get("CODEX_HOME")
    return Path(override) if override else context.home / ".codex"


def opencode_config_dir(context: Environment) -> Path:
    """`$OPENCODE_CONFIG_DIR` moves OpenCode's config; the default is
    `~/.config/opencode`."""
    override = context.env.get("OPENCODE_CONFIG_DIR")
    return Path(override) if override else context.home / ".config" / "opencode"


class InstallError(Exception):
    """A tier-1 refusal: environment or config-file trouble, with its fix."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def ensure_evidence(label: str, evidence_dir: Path) -> None:
    """The client is installed here — or the install is refused."""
    if not evidence_dir.is_dir():
        raise InstallError(
            f"{label} is not installed here: {evidence_dir} does not exist",
            "install the client first — neosian never creates another "
            "program's config directory",
        )


def read_document(path: Path) -> dict[str, Any] | None:
    """A client's config for a reader that never repairs: JSON, or TOML by
    suffix; None when it is missing or not a document."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        raw: Any = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def load_document(path: Path) -> dict[str, Any]:
    """The existing config as a dict, or {} when the file does not exist."""
    if not path.exists():
        return {}
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InstallError(f"cannot read {path}: {exc}", FIX_BY_HAND) from exc
    except json.JSONDecodeError as exc:
        raise InstallError(
            f"{path} is not valid JSON; refusing to rewrite it", FIX_BY_HAND
        ) from exc
    if not isinstance(raw, dict):
        raise InstallError(
            f"{path} is not a JSON object; refusing to rewrite it", FIX_BY_HAND
        )
    document: dict[str, Any] = raw
    return document


def write_document(path: Path, document: dict[str, Any]) -> None:
    write_text(path, json.dumps(document, indent=2) + "\n")


def write_text(path: Path, text: str) -> None:
    # Client configs carry other servers' credentials: an existing file
    # keeps its mode, a new one is born private — never the umask default.
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else PRIVATE_FILE
        atomic_write(path, text, mode=mode)
    except OSError as exc:
        raise InstallError(f"cannot write {path}: {exc}", FIX_BY_HAND) from exc
