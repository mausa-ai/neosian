"""PostgresStore pages its four listings, directly and through the
daemon over Postgres (NQ2 slice C, §8, §18.2) — the keyset statements
under both clocks, `FrozenClock` tying every row on `created_at`."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from neosian import PostgresStore, RemoteStore
from tests.support.postgres_server import remote_postgres
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
    @pytest.fixture(params=["python", "rust"])
    async def store(
        self,
        store: PostgresStore,
        postgres_dsn: str,
        tmp_path: Path,
        request: pytest.FixtureRequest,
    ) -> AsyncIterator[RemoteStore]:
        async with remote_postgres(
            store, postgres_dsn, tmp_path, request.param, _TOKEN
        ) as remote:
            yield remote
