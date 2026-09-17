"""Shared fixtures for the state-process unit tests.

Everything here is in-process and keyless: the app runs over
`httpx.ASGITransport`, the backing store is a FileStore the test keeps a
reference to (the substrate-planting seam), and the bearer token is a
fixed literal — auth is exercised, never bypassed.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian import RemoteStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.app import build_app
from tests.support.clock import ManualClock

TOKEN = "unit-test-token"
BASE_URL = "http://state-process"


class RawWire:
    """The app over ASGITransport with one bare token and no `RemoteStore`
    in the loop: literal JSON in, literal JSON out (ND, the wire's contract).
    A handler that leaks past the envelope is observed as its status, never
    as an exception in the test."""

    def __init__(self, http: httpx.AsyncClient, backing: FileStore) -> None:
        self.http = http
        self.backing = backing

    async def post(self, route: str, body: object) -> tuple[int, Any]:
        response = await self.http.post(f"/v1/{route}", json=body)
        return response.status_code, response.json()

    async def send(self, route: str, content: bytes, **headers: str) -> tuple[int, str]:
        response = await self.http.post(
            f"/v1/{route}", content=content, headers=headers
        )
        return response.status_code, response.text

    async def get(self, path: str) -> tuple[int, Any]:
        response = await self.http.get(path)
        return response.status_code, response.json()


@pytest.fixture
async def raw_wire(tmp_path: Path, manual_clock: ManualClock) -> AsyncIterator[RawWire]:
    backing = FileStore(tmp_path / "backing", clock=manual_clock)
    app = await build_app(backing, token=TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http:
        yield RawWire(http, backing)


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
