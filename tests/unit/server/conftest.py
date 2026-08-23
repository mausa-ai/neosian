"""Shared fixtures for the state-process unit tests.

Everything here is in-process and keyless: the app runs over
`httpx.ASGITransport`, the backing store is a FileStore the test keeps a
reference to (the substrate-planting seam), and the bearer token is a
fixed literal — auth is exercised, never bypassed.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from neosian import RemoteStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.app import build_app
from tests.unit.memory.conftest import ManualClock

TOKEN = "unit-test-token"
BASE_URL = "http://state-process"


@pytest.fixture
def manual_clock() -> ManualClock:
    return ManualClock()


class RemoteOverFile:
    """One backing FileStore, its app, and a connected RemoteStore."""

    def __init__(self, backing: FileStore, root: Path, remote: RemoteStore) -> None:
        self.backing = backing
        self.root = root
        self.remote = remote


@pytest.fixture
async def remote_over_file(
    tmp_path: Path, manual_clock: ManualClock
) -> AsyncIterator[RemoteOverFile]:
    root = tmp_path / "backing"
    backing = FileStore(root, clock=manual_clock)
    app = await build_app(backing, token=TOKEN)
    remote = await RemoteStore.connect(
        BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app)
    )
    try:
        yield RemoteOverFile(backing, root, remote)
    finally:
        await remote.aclose()
