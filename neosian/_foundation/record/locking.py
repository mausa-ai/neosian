"""Serialize a session's spool across hook processes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from neosian._foundation.shared.filelock import FileLock


@asynccontextmanager
async def session_lock(directory: Path, session: str) -> AsyncIterator[None]:
    async with FileLock(directory / f".{session}.lock"):
        yield
