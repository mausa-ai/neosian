"""Fixtures for the container conformance tier (`external_server`).

`RemoteStore` is pointed at a *running state-process container* — NM's
done-when — started by `scripts/container_test.sh` (`make
test-container`), which builds the image, runs one container per
backend, and exports the env below. Every test self-skips per test when
the env is absent — falsiness, never module-level (DESIGN §10); an
unset CI variable arrives as "".

Per-test isolation on a long-running server: the conformance kits
demand a function-scoped empty store, and both kits document their
`scope` / `conversation_id` fixtures as override points — the contract
classes override them with per-test unique names, so every test works
in a namespace no other test (and no server-side cache) has ever seen.
The script starts each container on a fresh volume, so the namespaces
are genuinely empty; a host-side wipe was rejected — macOS bind-mount
dentry caching lets the container see a half-stale directory. The
Postgres leg additionally truncates the serving schema's tables (no
DDL — the server's pooled statements stay valid). The backing store
stays in the test's hands for substrate planting — the ledger #36
latitude, from the server's side of the wire (§18.8).
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from neosian import PostgresStore, RemoteStore
from tests.external.postgres.conftest import plant_sql

URL_ENV = "NEOSIAN_TEST_SERVER_URL"
TOKEN_ENV = "NEOSIAN_TEST_SERVER_TOKEN"
ROOT_ENV = "NEOSIAN_TEST_SERVER_ROOT"
DSN_ENV = "NEOSIAN_TEST_POSTGRES_DSN"
SCHEMA_ENV = "NEOSIAN_TEST_SERVER_SCHEMA"

_TABLES = (
    "memories",
    "memory_versions",
    "memory_redactions",
    "conversations",
    "turns",
    "projections",
)


def _env_or_skip(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} not set — run scripts/container_test.sh")
    return value


class FileLeg:
    """The container over a volume FileStore, its root in hand."""

    def __init__(self, root: Path, remote: RemoteStore) -> None:
        self.root = root
        self.remote = remote


class PgLeg:
    """The container over a Postgres DSN, the backing store in hand."""

    def __init__(self, backing: PostgresStore, remote: RemoteStore) -> None:
        self.backing = backing
        self.remote = remote


@pytest.fixture
async def server_url() -> str:
    return _env_or_skip(URL_ENV)


@pytest.fixture
async def server_token() -> str:
    return _env_or_skip(TOKEN_ENV)


@pytest.fixture
async def file_leg(server_url: str, server_token: str) -> AsyncIterator[FileLeg]:
    root = Path(_env_or_skip(ROOT_ENV))
    remote = await RemoteStore.connect(server_url, token=server_token)
    try:
        yield FileLeg(root, remote)
    finally:
        await remote.aclose()


@pytest.fixture
async def pg_leg(server_url: str, server_token: str) -> AsyncIterator[PgLeg]:
    dsn = _env_or_skip(DSN_ENV)
    schema = _env_or_skip(SCHEMA_ENV)
    backing = PostgresStore(dsn, schema=schema)
    names = ", ".join(f'"{schema}".{table}' for table in _TABLES)
    await plant_sql(backing, f"TRUNCATE {names} CASCADE", {})
    remote = await RemoteStore.connect(server_url, token=server_token)
    try:
        yield PgLeg(backing, remote)
    finally:
        await remote.aclose()
        await backing.aclose()
