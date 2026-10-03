"""Cancellation-safe advisory locks for cooperating local processes.

Lock files are never unlinked: a waiter must keep competing on the same
inode. Worker-thread I/O finishes before a cancelled caller releases its
lock, so cancellation cannot let a second writer overtake an active one.
"""

from __future__ import annotations

import asyncio
import errno
import os
import sys
from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from pathlib import Path
from types import TracebackType
from typing import Any, Concatenate, Protocol

from neosian._foundation.shared.fileio import PRIVATE_FILE, private_mkdir

if sys.platform == "win32":  # pragma: no cover - Windows CI
    import msvcrt

    def _acquire(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

else:
    import fcntl

    def _acquire(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


class FileLock:
    """One asynchronous, non-reentrant lock over a stable local path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = asyncio.Lock()
        self._fd: int | None = None

    async def __aenter__(self) -> None:
        await self._local.acquire()
        fd: int | None = None
        try:
            private_mkdir(self.path.parent)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, PRIVATE_FILE)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            while True:
                try:
                    _acquire(fd)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    await asyncio.sleep(0.01)
            self._fd = fd
        except BaseException:
            if fd is not None:
                os.close(fd)
            self._local.release()
            raise

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._fd is not None
        os.close(self._fd)
        self._fd = None
        self._local.release()


class LockOwner(Protocol):
    _lock: FileLock


def locked[S: LockOwner, **P, R](
    method: Callable[Concatenate[S, P], Awaitable[R]],
) -> Callable[Concatenate[S, P], Coroutine[Any, Any, R]]:
    """Serialize a store method and drain its I/O before unlocking."""

    @wraps(method)
    async def call(self: S, /, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._lock:

            async def run() -> R:
                return await method(self, *args, **kwargs)

            task = asyncio.create_task(run())
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                if not task.cancelled():
                    task.exception()
                raise

    return call
