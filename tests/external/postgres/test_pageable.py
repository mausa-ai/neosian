"""PostgresStore pages its four listings, directly and through the
daemon over Postgres (NQ2 slice C, §8, §18.2) — the keyset statements
under both clocks, `FrozenClock` tying every row on `created_at`."""

from collections.abc import AsyncIterator

import httpx
import pytest

from neosian import PostgresStore, RemoteStore
from neosian._foundation.server.app import build_app
from tests.unit.memory.paging import FrozenClock, PagingSuite

pytestmark = [pytest.mark.external_postgres, pytest.mark.asyncio]

_TOKEN = "pageable-token"


class TestPostgresPaging(PagingSuite):
    pass


class TestPostgresPagingOnTies(PagingSuite):
    @pytest.fixture
    def manual_clock(self) -> FrozenClock:
        return FrozenClock()


class TestRemoteOverPostgresPaging(PagingSuite):
    @pytest.fixture
    async def store(self, store: PostgresStore) -> AsyncIterator[RemoteStore]:
        app = await build_app(store, token=_TOKEN)
        client = await RemoteStore.connect(
            "http://state-process", token=_TOKEN, transport=httpx.ASGITransport(app=app)
        )
        try:
            yield client
        finally:
            await client.aclose()
