"""FileStore's on-disk addressing — one truth for the store and its
mobility mixin (DESIGN §8 layout, §26.2).

    root/<scope segments, percent-encoded>/
        documents/<path>.md · versions/<path>.jsonl · redactions.jsonl
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import unquote

from neosian._foundation.memory.paths import path_segments
from neosian._foundation.memory.scope import Scope, parse_scope, scope_directory
from neosian._foundation.shared.exceptions import MemoryPathInvalidError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

DOCUMENTS: Final = "documents"
VERSIONS: Final = "versions"
REDACTIONS: Final = "redactions.jsonl"


def scope_dir(root: Path, scope: str) -> Path:
    return root.joinpath(*scope_directory(parse_scope(scope)))


def scope_from_directory(parts: Sequence[str]) -> Scope:
    """The inverse of `scope_directory`: components back to a validated
    scope (a foreign directory fails the grammar and is the caller's to
    skip)."""
    return parse_scope("/".join(unquote(part) for part in parts))


def doc_file(root: Path, scope: str, path: str) -> Path:
    segments = path_segments(path)
    candidate = scope_dir(root, scope).joinpath(
        DOCUMENTS, *segments[:-1], segments[-1] + ".md"
    )
    return _contained(root, candidate, path)


def journal_file(root: Path, scope: str, path: str) -> Path:
    segments = path_segments(path)
    candidate = scope_dir(root, scope).joinpath(
        VERSIONS, *segments[:-1], segments[-1] + ".jsonl"
    )
    return _contained(root, candidate, path)


def _contained(root: Path, candidate: Path, path: str) -> Path:
    if not candidate.resolve().is_relative_to(root):
        raise MemoryPathInvalidError(path, "escapes the store root")
    return candidate
