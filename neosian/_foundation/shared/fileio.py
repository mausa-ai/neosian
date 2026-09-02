"""Private file I/O — one deliberate mode, atomic replace, opt-in fsync.

FileStore's memory documents were `0600` by accident of `mkstemp` while
the version sidecar and the turn log took the umask default (`0644`) —
the review's MC-4. Everything the library writes on a user's disk is
private by decision now (ledger #126): files `0600`, directories `0700`,
applied on creation and never retro-fitted. A writer that must keep the
mode of a file it does not own — the MCP installer editing a client's
config — passes `mode=` explicitly. Neither writer creates directories:
`private_mkdir` is the caller's separate, visible act.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

PRIVATE_FILE: Final = 0o600
PRIVATE_DIR: Final = 0o700


def private_mkdir(directory: Path) -> None:
    """`mkdir -p` with every *newly created* level at `PRIVATE_DIR`.

    `Path.mkdir(mode=…, parents=True)` applies the mode to the leaf only;
    the levels are created one by one so none is born world-readable.
    """
    missing: list[Path] = []
    current = directory
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    for level in reversed(missing):
        level.mkdir(mode=PRIVATE_DIR, exist_ok=True)


def atomic_write(
    file: Path, text: str, *, mode: int = PRIVATE_FILE, fsync: bool = False
) -> None:
    """Write via a same-directory temp file + `os.replace` — never partial.

    The mode is set explicitly (umask-free) before the replace, so the
    destination is born with it. `fsync=True` flushes the temp file to
    disk first: the replace then never outruns the bytes it points at.
    """
    fd, temp_name = tempfile.mkstemp(dir=file.parent, prefix=".neosian-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            if fsync:
                handle.flush()
                os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, file)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def append_line(file: Path, text: str, *, fsync: bool = False) -> None:
    """Append in one write; a new file is born `PRIVATE_FILE`, an existing
    one keeps its mode."""
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
    with os.fdopen(fd, "a", encoding="utf-8", newline="") as handle:
        handle.write(text)
        if fsync:
            handle.flush()
            os.fsync(handle.fileno())
