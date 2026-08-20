"""PostgresStore surface without a server — construction is pure, the
driver loads only on first pool use, and the core import stays
driver-free (keyless boot)."""

import subprocess
import sys

import pytest

from neosian import PostgresStore
from neosian._foundation.postgres.driver import load_driver
from neosian._foundation.shared.exceptions import ConfigurationError

_DSN = "postgresql://nobody@localhost:1/nowhere"


@pytest.mark.unit
def test_construction_is_pure_validation() -> None:
    store = PostgresStore(_DSN)
    assert type(store).supports_optimistic_concurrency is True
    assert 'CREATE SCHEMA IF NOT EXISTS "neosian";' in store.schema_sql()


@pytest.mark.unit
def test_invalid_schema_name_raises_configuration_error() -> None:
    for bad in ("a-b", "1x", 'x"y', "Neosian", "x" * 64):
        with pytest.raises(ConfigurationError):
            PostgresStore(_DSN, schema=bad)


@pytest.mark.unit
async def test_aclose_before_use_is_an_idempotent_no_op() -> None:
    store = PostgresStore(_DSN)
    await store.aclose()
    await store.aclose()


@pytest.mark.unit
async def test_async_with_needs_no_io() -> None:
    async with PostgresStore(_DSN) as store:
        assert isinstance(store, PostgresStore)


@pytest.mark.unit
def test_missing_extra_names_the_install_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "psycopg_pool", None)
    with pytest.raises(ImportError, match=r"neosian\[postgres\]"):
        load_driver()


@pytest.mark.unit
def test_core_imports_stay_driver_free() -> None:
    """`import neosian` and both facades must never pull psycopg."""
    code = (
        "import sys; "
        "import neosian, neosian.memory, neosian.conversation; "
        "assert not any(m.startswith('psycopg') for m in sys.modules), "
        "sorted(m for m in sys.modules if m.startswith('psycopg'))"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
