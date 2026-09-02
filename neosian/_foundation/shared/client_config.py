"""Editing another program's config file — the installers' shared half.

`neosian mcp install` (DESIGN §14.5) and `neosian record install` (§20.9)
print a registration by default and apply it only with --write, merging
key-preserving into a JSON document the client owns. Three rules, held
here once: a missing client home is refused, never created; an existing
file keeps its mode and a new one is born private; a file that is not a
JSON object is refused, never rewritten. Pure over an injected
`Environment`: path resolution reads no ambient state, which keeps the
suites monkeypatch-free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.shared.fileio import PRIVATE_FILE, atomic_write

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

FIX_BY_HAND: Final = (
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
    # Client configs carry other servers' credentials: an existing file
    # keeps its mode, a new one is born private — never the umask default.
    text = json.dumps(document, indent=2) + "\n"
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else PRIVATE_FILE
        atomic_write(path, text, mode=mode)
    except OSError as exc:
        raise InstallError(f"cannot write {path}: {exc}", FIX_BY_HAND) from exc
