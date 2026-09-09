"""The one place psycopg is imported (function-local, on first pool use).

Every other module in this package is importable without the driver
installed — `import neosian` and both facades stay driver-free, pinned by
a subprocess test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

_INSTALL_HINT = (
    "PostgresStore needs psycopg, the 'postgres' extra — "
    "uv add 'neosian[postgres]' (or pip install 'neosian[postgres]')"
)


@dataclass(frozen=True, slots=True)
class Driver:
    """The loaded driver surface: the pool class and the exceptions a
    single-statement mutation may retry on (unique-violation races and
    deadlocks between opposing renames)."""

    pool_class: type[AsyncConnectionPool]
    retryable: tuple[type[Exception], ...]


def load_driver() -> Driver:
    """Import psycopg, raising a helpful ImportError without the extra."""
    try:
        from psycopg.errors import DeadlockDetected, UniqueViolation
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc
    return Driver(
        pool_class=AsyncConnectionPool,
        retryable=(UniqueViolation, DeadlockDetected),
    )
