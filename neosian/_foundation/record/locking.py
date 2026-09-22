"""Serialize one session's hook processes, including an awaited store write.

The OS releases an advisory lock if a hook dies. Lock files remain: unlinking
one while another process waits would give later hooks a different lock.
This protects the spool, not a FileStore root shared by multiple writers.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from neosian._foundation.shared.fileio import PRIVATE_FILE, private_mkdir

if sys.platform == "win32":  # pragma: no cover - Windows uses byte-range locks
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@asynccontextmanager
async def session_lock(directory: Path, session: str) -> AsyncIterator[None]:
    private_mkdir(directory)
    fd = os.open(directory / f".{session}.lock", os.O_CREAT | os.O_RDWR, PRIVATE_FILE)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        while True:
            try:
                _lock(fd)
                break
            except (BlockingIOError, PermissionError):
                await asyncio.sleep(0.01)
        yield
    finally:
        os.close(fd)
