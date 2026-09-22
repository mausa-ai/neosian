"""A hook's session lock survives cancellation and is released on process exit."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from neosian._foundation.record.locking import session_lock


async def test_another_process_owns_the_lock_until_it_exits(tmp_path: Path) -> None:
    script = """
import asyncio, sys
from pathlib import Path
from neosian._foundation.record.locking import session_lock
async def hold():
    async with session_lock(Path(sys.argv[1]), 'session'):
        print('locked', flush=True)
        await asyncio.Event().wait()
asyncio.run(hold())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
    )

    async def acquire() -> None:
        async with session_lock(tmp_path, "session"):
            pass

    try:
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"locked\n"
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(acquire(), 0.05)
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    await asyncio.wait_for(acquire(), 5)
    assert (tmp_path / ".session.lock").exists()
