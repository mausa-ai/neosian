"""PostgresStore surface without a server — construction is pure, the
driver loads only on first pool use, and the core import stays
driver-free (keyless boot)."""

import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast

import pytest

from neosian import PostgresStore
from neosian._foundation.postgres.driver import Driver, load_driver
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

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
def test_pool_sizing_is_pure_construction() -> None:
    store = PostgresStore(_DSN, min_size=1, max_size=8, pool_timeout=1.5)
    assert isinstance(store, PostgresStore)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("min_size", "max_size", "pool_timeout"),
    [(0, None, 30.0), (4, 2, 30.0), (4, None, 0.0), (4, None, -1.0)],
)
def test_invalid_pool_sizing_raises_configuration_error(
    min_size: int, max_size: int | None, pool_timeout: float
) -> None:
    with pytest.raises(ConfigurationError):
        PostgresStore(
            _DSN, min_size=min_size, max_size=max_size, pool_timeout=pool_timeout
        )


@pytest.mark.unit
async def test_pool_sizing_reaches_the_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mistyped psycopg kwarg would silently fall back to its defaults."""
    captured: dict[str, Any] = {}

    class _RecordingPool:
        def __init__(self, dsn: str, **kwargs: Any) -> None:
            captured["dsn"] = dsn
            captured.update(kwargs)

        async def open(self) -> None:
            return None

    fake = Driver(
        pool_class=cast("type[AsyncConnectionPool]", _RecordingPool), retryable=()
    )
    monkeypatch.setattr("neosian._foundation.postgres.pool.load_driver", lambda: fake)
    store = PostgresStore(_DSN, min_size=2, max_size=8, pool_timeout=1.5)
    await store._pool._ensure_pool()  # noqa: SLF001 — the substrate hook idiom
    assert captured["min_size"] == 2
    assert captured["max_size"] == 8
    assert captured["timeout"] == 1.5
    assert captured["open"] is False
    assert captured["kwargs"] == {"autocommit": True}


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
