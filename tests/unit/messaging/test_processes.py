"""True process races, independent FileStore objects and lock recovery."""

import asyncio
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from neosian import FileStore, MemoryConfig
from neosian._foundation.conversation import file_turns
from neosian._foundation.conversation.types import ConversationTurn
from neosian.messaging import Mailbox, MessageTarget

from .conftest import SCOPE

_CHILD = """
import asyncio, sys
from neosian import FileStore, MemoryConfig, Mount
from neosian.messaging import Mailbox, MessageTarget
async def main():
    root, session, message = sys.argv[1:]
    box = Mailbox(MemoryConfig(FileStore(root), (Mount("user:test/proj:mail", "project"),)), session=session)
    for n in range(6):
        await box.send(MessageTarget("user:test/proj:mail"), f"{session}:{n}")
    try:
        await box.update("user:test/proj:mail", message, "claim", occurrence=1)
        print("won")
    except ValueError:
        print("lost")
asyncio.run(main())
"""


async def test_independent_processes_do_not_lose_messages(
    memory: MemoryConfig, tmp_path: Path
) -> None:
    box = Mailbox(memory, session="parent")
    item = (await box.send(MessageTarget(SCOPE), "claim once", actionable=True)).message

    async def child(number: int) -> str:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _CHILD,
            str(tmp_path / "store"),
            f"child-{number}",
            item.id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        assert process.returncode == 0, err.decode()
        return out.decode().strip()

    results = await asyncio.gather(*(child(n) for n in range(4)))
    assert results.count("won") == 1
    assert results.count("lost") == 3
    assert len(await box.all()) == 25


async def test_killed_lock_holder_does_not_strand_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    script = """
import asyncio, sys
from pathlib import Path
from neosian._foundation.shared.filelock import FileLock
async def main():
    async with FileLock(Path(sys.argv[1]) / ".store.lock"):
        print("locked", flush=True)
        await asyncio.Event().wait()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, str(root), stdout=asyncio.subprocess.PIPE
    )
    assert process.stdout is not None
    try:
        assert await process.stdout.readline() == b"locked\n"
    finally:
        process.kill()
        await process.wait()
    doc = await asyncio.wait_for(FileStore(root).write(SCOPE, "after", "recovered"), 3)
    assert doc.content == "recovered"


async def test_cancellation_drains_thread_before_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()

    def slow_scan(
        _root: Path, _ids: Sequence[str] | None, _terms: Sequence[str], _limit: int
    ) -> tuple[ConversationTurn, ...]:
        entered.set()
        assert release.wait(3)
        return ()

    monkeypatch.setattr(file_turns, "search_files", slow_scan)
    root = tmp_path / "shared"
    scan = asyncio.create_task(FileStore(root).search_turns("needle"))
    assert await asyncio.to_thread(entered.wait, 3)
    scan.cancel()
    writer = asyncio.create_task(FileStore(root).write(SCOPE, "after", "safe"))
    try:
        await asyncio.sleep(0.03)
        scan.cancel()  # Repeated cancellation cannot drop the lock either.
        await asyncio.sleep(0.01)
        assert not writer.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await scan
    assert (await asyncio.wait_for(writer, 3)).content == "safe"
