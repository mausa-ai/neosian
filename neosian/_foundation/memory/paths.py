"""The document-path grammar (DESIGN §8), validated — never interpreted.

Paths are extension-free logical names (`FileStore` appends `.md`).
Directory semantics belong to the tool layer (C7); the store sees flat
strings with `/` as a permitted character class boundary only.
"""

from __future__ import annotations

import re
from typing import Final

from neosian._foundation.shared.exceptions import MemoryPathInvalidError

PATH_MAX_LENGTH: Final = 512
PATH_MAX_SEGMENTS: Final = 16

# Anchored \A…\Z like SCOPE_PATTERN; literal charset, never \w (Unicode).
DOCUMENT_PATH_PATTERN: Final = re.compile(
    r"\A[A-Za-z0-9_.-]{1,128}(?:/[A-Za-z0-9_.-]{1,128})*\Z"
)


def validate_document_path(path: str) -> None:
    """Validate a logical document path against the grammar.

    Raises:
        MemoryPathInvalidError: On any shape violation. Only segments that
            are exactly `.` or `..` are rejected — `..foo` is legal.
    """
    if len(path) > PATH_MAX_LENGTH:
        raise MemoryPathInvalidError(path, f"longer than {PATH_MAX_LENGTH} characters")
    if not DOCUMENT_PATH_PATTERN.match(path):
        raise MemoryPathInvalidError(
            path, "segments must be [A-Za-z0-9_.-]{1,128} joined by '/'"
        )
    segments = path.split("/")
    if len(segments) > PATH_MAX_SEGMENTS:
        raise MemoryPathInvalidError(path, f"more than {PATH_MAX_SEGMENTS} segments")
    for segment in segments:
        if segment in (".", ".."):
            raise MemoryPathInvalidError(path, f"segment {segment!r} is reserved")


def path_segments(path: str) -> tuple[str, ...]:
    """Split a validated path into its segments."""
    return tuple(path.split("/"))
